#!/usr/bin/env python3
"""
CyberPower PDU control utility via SSH.

Features:
- Query or change outlet state(s) with rich terminal output.
- Control multiple outlets via comma-separated list (e.g., --outlet 1,3,5).
- Verifies final state of outlets after on/off/reboot actions.
- Reads SSH credentials from ~/.config/rig_control/.pdu_credentials.
- Reads outlet metadata (names, descriptions) from system_config.yml.
- Uses 'rich' for logging, status table, and styled output.

Written by Ratan Othayoth, PhD, modified by Jeremy Delahanty for pancakes
"""

from __future__ import annotations

# Standard Library Imports
import argparse
import configparser
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Third-party Imports
import pexpect
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich import print as rprint # Use alias to avoid conflict with built-in print
from rich.text import Text

# Logger Configuration
log = logging.getLogger(__name__)

# Constants
HOME = Path.home()
CREDENTIALS_FILE = HOME / ".config" / "rig_control" / ".pdu_credentials"
SYSTEM_CFG_FILE = Path("system_config.yml") # Assumed in current directory
DEFAULT_SSH_TIMEOUT = 10  # seconds
VERIFICATION_DELAY_S = 5 # seconds to wait before checking status after on/off
VERIFICATION_DELAY_REBOOT_S = 10 # seconds for reboot (PDU internal delays vary)
MAX_OUTLETS = 8

# Exception Class
class PDUError(Exception):
    """Raised for PDU connection, command execution, or parsing errors."""

# Outlet Metadata Loading
def _load_outlet_map(config_path: Path, pdu_ip: str) -> Dict[str, Dict[str, Optional[str]]]:
    """Loads outlet name/description mapping from a YAML config file."""
    log.info("Attempting to load outlet metadata from %s for PDU %s", config_path, pdu_ip)
    if not config_path.is_file():
        log.warning("System config file not found: %s", config_path)
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)

        if not isinstance(config_data, dict) or "pdus" not in config_data:
            log.warning("Invalid format in %s: Missing 'pdus' key.", config_path)
            return {}

        for pdu_config in config_data.get("pdus", []):
            if pdu_config.get("ip_address") == pdu_ip:
                outlets = pdu_config.get("outlets", {})
                outlet_map = {
                    str(k): (v if isinstance(v, dict) else {})
                    for k, v in outlets.items()
                }
                log.info("Successfully loaded metadata for %d outlets from %s", len(outlet_map), config_path)
                return outlet_map

        log.warning("No configuration found for PDU IP %s in %s", pdu_ip, config_path)
        return {}

    except yaml.YAMLError as exc:
        log.warning("Error parsing YAML file %s: %s", config_path, exc)
    except IOError as exc:
        log.warning("Could not read system config file %s: %s", config_path, exc)
    except Exception as exc:
        log.error("Unexpected error loading outlet map from %s: %s", config_path, exc, exc_info=True)
    return {}

# Credential Loading
def _read_credentials(path: Path) -> Tuple[str, str]:
    """Reads PDU username and password from a config file."""
    log.info("Reading credentials from %s", path)
    if not path.is_file():
        log.error("Credentials file not found: %s", path)
        raise PDUError(f"Credentials file not found: {path}")

    try:
        parser = configparser.ConfigParser()
        parser.read(path)
        if "PDU_DEFAULT" not in parser:
             raise PDUError(f"Missing [PDU_DEFAULT] section in {path}")
        if "PDU_USER" not in parser["PDU_DEFAULT"] or "PDU_PASSWORD" not in parser["PDU_DEFAULT"]:
             raise PDUError(f"Missing PDU_USER or PDU_PASSWORD in [PDU_DEFAULT] section of {path}")

        username = parser.get("PDU_DEFAULT", "PDU_USER")
        password = parser.get("PDU_DEFAULT", "PDU_PASSWORD")
        log.info("Successfully read credentials for user [i]%s[/i]", username)
        return username, password
    except configparser.Error as exc:
        log.error("Error reading credentials file %s: %s", path, exc)
        raise PDUError(f"Error reading credentials file {path}: {exc}") from exc
    except Exception as exc:
        log.error("Unexpected error reading credentials from %s: %s", path, exc, exc_info=True)
        raise PDUError(f"Unexpected error reading credentials from {path}: {exc}") from exc


# PDU Driver Class
class CyberPowerPDU:
    """Class for SSH communication with a CyberPower PDU"""
    _PROMPT = r"CyberPower\s*>"
    _SSH_OPTIONS = (
        "-oHostKeyAlgorithms=+ssh-rsa " 
        "-o StrictHostKeyChecking=accept-new " # Accept new host keys without prompting
        "-c aes256-cbc" 
    )
    _STATUS_LINE_RE = re.compile(r"^\s*(\d+)\s+([\w:-]+)\s+(On|Off)\b", re.IGNORECASE) # Regex to match outlet status lines

    def __init__(self, host: str, username: str, password: str, *, timeout: int = DEFAULT_SSH_TIMEOUT):
        """Initializes the PDU controller."""
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        log.debug("PDU object initialized for host %s", self.host)

    def _connect(self) -> pexpect.spawn: 
        """Establishes an SSH connection to the PDU by spawning a pexpect thread."""
        ssh_command = f"ssh {self._SSH_OPTIONS} {self.username}@{self.host}"
        log.info("Connecting to PDU at %s...", self.host)
        log.debug("Executing SSH command: %s", ssh_command)
        child = None
        try:
            child = pexpect.spawn(ssh_command, timeout=self.timeout, encoding="utf-8", echo=False)
            idx = child.expect(["password: ", r"Are you sure you want to continue connecting"], timeout=self.timeout)
            if idx == 1:
                log.info("Adding new host key for %s", self.host)
                child.sendline("yes")
                child.expect("password: ", timeout=self.timeout)
            child.sendline(self.password)
            child.expect(self._PROMPT, timeout=self.timeout)
            log.info("Successfully connected to PDU %s", self.host)
            return child
        except pexpect.TIMEOUT:
            log.error("Timeout connecting to PDU %s", self.host)
            raise PDUError(f"Timeout connecting to PDU {self.host}")
        except pexpect.EOF:
            log.error("EOF received while connecting to PDU %s. Authentication failed or connection closed.", self.host)
            log.debug("Pexpect before EOF: %s", getattr(child, 'before', 'N/A'))
            log.debug("Pexpect after EOF: %s", getattr(child, 'after', 'N/A'))
            raise PDUError(f"Authentication failed or connection lost to PDU {self.host}")
        except Exception as exc:
            log.error("Unexpected error connecting to %s: %s", self.host, exc, exc_info=True)
            raise PDUError(f"Unexpected error connecting to {self.host}: {exc}") from exc

    def _exec(self, child: pexpect.spawn, command: str) -> str:
        """Executes a command on the connected PDU and returns the output."""
        log.info("Executing command: '%s'", command)
        try:
            child.sendline(command)
            child.sendcontrol("m") # Required by some PDUs
            child.expect(self._PROMPT, timeout=self.timeout)
            output = getattr(child, 'before', '').strip()
            log.debug("Raw output from '%s':\n%s", command, output)

            if output.startswith(command):
                output = output[len(command):].strip()
            log.debug("Cleaned output from '%s':\n%s", command, output)
            log.info("Command '%s' executed successfully", command)
            return output
        except pexpect.TIMEOUT:
            log.error("Timeout waiting for response to command '%s' on PDU %s", command, self.host)
            raise PDUError(f"Timeout executing command '{command}' on {self.host}")
        except pexpect.EOF:
            log.error("EOF received while executing command '%s' on PDU %s. Connection lost.", command, self.host)
            raise PDUError(f"Connection lost while executing command '{command}' on {self.host}")
        except Exception as exc:
            log.error("Unexpected error executing command '%s' on %s: %s", command, self.host, exc, exc_info=True)
            raise PDUError(f"Unexpected error executing command '{command}' on {self.host}: {exc}") from exc


    def _parse_status(self, raw_output: str) -> Dict[str, str]:
        """Parses the raw output of 'oltsta show' command."""
        status: Dict[str, str] = {}
        lines = raw_output.splitlines()
        log.debug("Parsing %d lines of status output.", len(lines))
        for line in lines:
            match = self._STATUS_LINE_RE.match(line)
            if match:
                outlet_id = match.group(1)
                outlet_state = match.group(3).upper()
                status[outlet_id] = outlet_state
                log.debug("Parsed status: Outlet %s = %s", outlet_id, outlet_state)
        if not status and raw_output:
             log.warning("Could not parse any outlet status from output.")
             log.debug("Raw output for failed parse:\n%s", raw_output)
        return status

    def get_outlet_status(self) -> Dict[str, str]:
        """Retrieves the status (ON/OFF) of all outlets."""
        log.info("Retrieving outlet status from PDU %s", self.host)
        child = None
        try:
            child = self._connect()
            raw_output = self._exec(child, "oltsta show")
            status = self._parse_status(raw_output)
            log.info("Successfully retrieved status for %d outlets", len(status))
            return status
        except PDUError as exc:
             log.error("Failed to get outlet status: %s", exc)
             raise
        except Exception as exc:
             log.error("Unexpected error getting outlet status: %s", exc, exc_info=True)
             raise PDUError(f"Unexpected error getting outlet status: {exc}") from exc
        finally:
            if child and child.isalive():
                log.debug("Closing SSH connection.")
                child.close()

    def set_outlet_state(self, outlet_id: str, state: str) -> None:
        """Sets the state of a specific outlet or all outlets using 'oltctrl'."""
        state_lower = state.lower()
        if state_lower not in {"on", "off", "reboot"}:
            log.error("Invalid state '%s' requested. Must be 'on', 'off', or 'reboot'.", state)
            raise ValueError("State must be 'on', 'off', or 'reboot'")

        if outlet_id.lower() != "all":
            try:
                outlet_num = int(outlet_id)
                if not 1 <= outlet_num <= MAX_OUTLETS:
                    log.error("Invalid outlet ID '%s' requested. Must be 1-%d or 'all'.", outlet_id, MAX_OUTLETS)
                    raise ValueError(f"Outlet ID must be between 1 and {MAX_OUTLETS}, or 'all'")
            except ValueError:
                 log.error("Invalid outlet ID '%s' requested. Must be numeric (1-%d) or 'all'.", outlet_id, MAX_OUTLETS)
                 raise ValueError(f"Outlet ID must be numeric (1-{MAX_OUTLETS}) or 'all'") from None

        log.info("Setting outlet %s to state '%s' on PDU %s", outlet_id, state_lower, self.host)
        command = f"oltctrl index {outlet_id} act {state_lower}"
        child = None
        try:
            child = self._connect()
            self._exec(child, command)
            log.info("Successfully set outlet %s to %s", outlet_id, state_lower)
        except PDUError as exc:
             log.error("Failed to set outlet %s to %s: %s", outlet_id, state_lower, exc)
             raise
        except Exception as exc:
             log.error("Unexpected error setting outlet %s state: %s", outlet_id, exc, exc_info=True)
             raise PDUError(f"Unexpected error setting outlet {outlet_id} state: {exc}") from exc
        finally:
            if child and child.isalive():
                log.debug("Closing SSH connection.")
                child.close()

# CLI Printing
def _pretty_print_status_rich(pdu_ip: str, status: Dict[str, str], meta: Dict[str, Dict[str, Any]]):
    """Formats and prints the outlet status using rich.table.Table."""
    log.debug("Formatting status for %d potential outlets using Rich.", MAX_OUTLETS)

    table = Table(title=f"PDU Status ([cyan]{pdu_ip}[/])", show_header=True, header_style="bold magenta", border_style="dim", expand=False)
    table.add_column("Outlet", style="dim white", width=6, justify="center")
    table.add_column("State", justify="center", width=5)
    table.add_column("Name", style="yellow", min_width=10, no_wrap=True)
    table.add_column("Description", overflow="fold")

    for i in range(1, MAX_OUTLETS + 1):
        outlet_key = str(i)
        state_str = status.get(outlet_key, "??")
        outlet_meta = meta.get(outlet_key, {})
        name = outlet_meta.get("name", "")
        description = outlet_meta.get("description", "")

        if state_str.upper() == "ON":
            state_text = Text("ON", style="bold green")
        elif state_str.upper() == "OFF":
            state_text = Text("OFF", style="bold red")
        else:
            state_text = Text(state_str, style="bold yellow")

        table.add_row(
            outlet_key,
            state_text,
            name,
            description
        )

    console = Console()
    console.print(table)

# Parse comma-separated outlets
def _parse_outlet_list(outlet_str: str) -> List[int]:
    """Parses a comma-separated string of outlets into a list of valid integers."""
    if outlet_str.lower() == "all":
         raise ValueError("Internal error: _parse_outlet_list should not be called with 'all'")

    parsed_outlets: Set[int] = set()
    invalid_parts: List[str] = []

    parts = outlet_str.split(',')
    for part in parts:
        part_stripped = part.strip()
        if not part_stripped:
            continue
        try:
            outlet_num = int(part_stripped)
            if 1 <= outlet_num <= MAX_OUTLETS:
                parsed_outlets.add(outlet_num)
            else:
                invalid_parts.append(f"{part_stripped} (out of range 1-{MAX_OUTLETS})")
        except ValueError:
            invalid_parts.append(f"{part_stripped} (not a number)")

    if invalid_parts:
        raise ValueError(f"Invalid outlet values specified: {'; '.join(invalid_parts)}")

    return sorted(list(parsed_outlets))

def main():
    """Parses arguments and executes the requested PDU action."""
    start_time = time.monotonic()

    parser = argparse.ArgumentParser(
        description="Control CyberPower PDU outlets via SSH using Rich for output.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--host", required=True, help="IP address or hostname of the PDU.")
    parser.add_argument("--action", choices=["on", "off", "reboot", "status"], required=True,
                        help="Action to perform:\n"
                             "  on      - Turn outlet(s) ON\n"
                             "  off     - Turn outlet(s) OFF\n"
                             "  reboot  - Use PDU's native reboot for outlet(s)\n"
                             "  status  - Display outlet status table")
    parser.add_argument("--outlet", default="all",
                        help=f"Outlet number (1-{MAX_OUTLETS}), 'all', or comma-separated list (e.g., 1,3,5).")
    parser.add_argument("--verify", action="store_true", help="Verify final outlet state after action (adds delay).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")

    args = parser.parse_args()

    # Configure Rich logger
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(
            rich_tracebacks=True,
            markup=True,
            show_path=args.verbose,
            log_time_format="[%X]"
            )]
    )

    log.info("PDU Control Utility started.")
    if args.verbose:
        log.debug("Debug logging enabled.")
    log.debug("Parsed arguments: %s", args)

    error_console = Console(stderr=True, style="bold red")

    # Make variables for verification checking later
    acted_upon_outlets: List[int] = []
    expected_final_state: Optional[str] = None # 'ON' or 'OFF'
    verification_needed = False

    try:
        username, password = _read_credentials(CREDENTIALS_FILE)
        outlet_metadata = _load_outlet_map(SYSTEM_CFG_FILE, args.host)
        pdu = CyberPowerPDU(args.host, username, password, timeout=DEFAULT_SSH_TIMEOUT)

        if args.action == "status":
            log.info("Action: Get status for PDU %s", args.host)
            current_status = pdu.get_outlet_status()
            _pretty_print_status_rich(args.host, current_status, outlet_metadata)

        # Handle actions that operate on outlets (on, off, reboot)
        elif args.action in {"on", "off", "reboot"}:
            action_str = args.action.upper()
            target_outlets_str = args.outlet
            verification_needed = args.verify # Check if user requested verification

            # Determine expected final state ('reboot' ends in 'ON')
            expected_final_state = 'ON' if args.action in ['on', 'reboot'] else 'OFF'

            log.info("Action: %s for outlet(s) '%s' on PDU %s", action_str, target_outlets_str, args.host)

            if target_outlets_str.lower() == "all":
                log.info("Applying action %s to all outlets.", action_str)
                pdu.set_outlet_state("all", args.action)
                style = "green" if args.action == "on" else "red" if args.action == "off" else "yellow"
                rprint(f"Action [bold {style}]{action_str}[/] applied to [cyan]all[/] outlets.")
                # If verifying 'all', target list is 1 through MAX_OUTLETS
                if verification_needed:
                    acted_upon_outlets = list(range(1, MAX_OUTLETS + 1))

            else:
                # Parse comma-separated list
                try:
                    outlet_numbers = _parse_outlet_list(target_outlets_str)
                    if not outlet_numbers:
                         log.warning("No valid outlet numbers specified in '%s'", target_outlets_str)
                         rprint(f"[yellow]Warning:[/yellow] No valid outlets found in '{target_outlets_str}'. No action taken.")
                         sys.exit(0)

                    acted_upon_outlets = outlet_numbers # Store for verification
                    log.info("Applying action %s sequentially to outlets: %s", action_str, ', '.join(map(str, outlet_numbers)))

                    success_count = 0
                    for outlet_num in outlet_numbers:
                        outlet_id_str = str(outlet_num)
                        try:
                            log.info("Processing outlet %s...", outlet_id_str)
                            pdu.set_outlet_state(outlet_id_str, args.action)
                            style = "green" if args.action == "on" else "red" if args.action == "off" else "yellow"
                            rprint(f"Outlet [cyan]{outlet_id_str}[/cyan] action [bold {style}]{action_str}[/] initiated.")
                            success_count += 1
                        except (PDUError, ValueError) as outlet_exc:
                            log.error("Failed action %s for outlet %s: %s", action_str, outlet_id_str, outlet_exc)
                            error_console.print(f"Error initiating action for outlet {outlet_id_str}: {outlet_exc}")
                            # Remove from list to check if initiation failed
                            if outlet_num in acted_upon_outlets:
                                acted_upon_outlets.remove(outlet_num)

                    log.info("Finished initiating action %s. Successful initiations: %d/%d", action_str, success_count, len(outlet_numbers))
                    if success_count < len(outlet_numbers):
                        rprint(f"[yellow]Warning:[/yellow] Action initiation failed for {len(outlet_numbers) - success_count} outlet(s). Check logs.")
                        # Continue to verify the ones that were successfully initiated

                except ValueError as parse_exc:
                    log.error("Invalid value for --outlet argument: %s", parse_exc)
                    error_console.print(f"Error: {parse_exc}")
                    sys.exit(1)

        # Verification state check
        if verification_needed and acted_upon_outlets and expected_final_state:
            delay = VERIFICATION_DELAY_REBOOT_S if args.action == 'reboot' else VERIFICATION_DELAY_S
            log.info("Waiting %d seconds before state verification...", delay)
            rprint(f"Waiting {delay}s for PDU state to settle before verification...")
            time.sleep(delay)

            log.info("Verifying final outlet states...")
            rprint("Verifying final outlet states...")
            mismatched_outlets: Dict[int, str] = {} # Store outlet_num: actual_state
            try:
                final_status = pdu.get_outlet_status()

                for outlet_num in acted_upon_outlets:
                    outlet_id_str = str(outlet_num)
                    actual_state = final_status.get(outlet_id_str, "UNKNOWN") # Get actual state or UNKNOWN

                    if actual_state.upper() != expected_final_state:
                        log.warning("Verification failed for outlet %s: Expected %s, got %s",
                                    outlet_id_str, expected_final_state, actual_state)
                        mismatched_outlets[outlet_num] = actual_state
                    else:
                        log.debug("Verification success for outlet %s: State is %s", outlet_id_str, actual_state)

                # Report verification results
                if not mismatched_outlets:
                    rprint("[bold green]Verification successful:[/bold green] All target outlets are in the expected state.")
                else:
                    mismatched_list = ", ".join(f"{num} (is {state})" for num, state in mismatched_outlets.items())
                    rprint(f"[bold yellow]Verification failed for some outlets:[/bold yellow] Expected state was {expected_final_state}.")
                    rprint(f"  Mismatched outlets: [cyan]{mismatched_list}[/cyan]")

            except PDUError as verify_exc:
                log.error("Failed to get status during verification: %s", verify_exc)
                error_console.print(f"Error during verification check: {verify_exc}")
                # Exit with error code as verification couldn't be completed
                sys.exit(1)


    # Error Handling
    except PDUError as exc:
        log.error("PDU operation failed: %s", exc)
        error_console.print(f"Error: {exc}")
        sys.exit(1)
    except ValueError as exc:
         log.error("Invalid input value: %s", exc)
         error_console.print(f"Error: {exc}")
         sys.exit(1)
    except KeyboardInterrupt:
         log.warning("Operation cancelled by user.")
         error_console.print("\nOperation cancelled.")
         sys.exit(1)
    except Exception as exc:
        log.exception("An unexpected error occurred during execution.")
        error_console.print(f"An unexpected error occurred: {exc}")
        sys.exit(1)
    finally:
        elapsed = time.monotonic() - start_time
        log.info("PDU Control Utility finished in %.2f seconds.", elapsed)


if __name__ == "__main__":
    main()