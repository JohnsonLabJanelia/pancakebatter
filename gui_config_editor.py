#!/usr/bin/env python3
"""Local web editor for hosts/<hostname>/config.yml (or the captured draft).

Edits the things that cannot be captured from the machine: NIC roles / IPs,
transceiver and NIC part/serial numbers (when not read by `sudo capture_inventory.py`),
and the cameras and lenses plugged into each port. Saving validates against the
system_config v1 schema first and keeps a timestamped backup of the old file.

    ./gui_config_editor.py                 # opens http://127.0.0.1:8765/?token=...
    ./gui_config_editor.py --file hosts/dumpling/config.captured.yml
    ssh -L 8765:127.0.0.1:8765 rig         # to use it from your laptop

Binds to 127.0.0.1 only and requires the per-run token printed at startup.
PyYAML rewrites the file, so comments inside the YAML are not preserved
(leading '#' lines at the top are).
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

import hostconfig
from configio import load, save


def default_file() -> Path:
    for name in ("config.yml", "config.captured.yml"):
        path = hostconfig.host_file(name)
        if path.exists():
            return path
    raise SystemExit(f"no config for {hostconfig.host_name()}: run ./capture_inventory.py first (or pass --file)")


def make_handler(path: Path, token: str, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            # Host check defeats DNS rebinding; token defeats other local pages/users.
            if self.headers.get("Host") not in allowed_hosts:
                return False
            supplied = self.headers.get("X-Token") or parse_qs(urlparse(self.path).query).get("token", [""])[0]
            return secrets.compare_digest(supplied, token)

        def do_GET(self):
            if not self._authorized():
                return self._send(403, b'{"error":"forbidden"}')
            route = urlparse(self.path).path
            if route == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif route == "/api/config":
                self._send(200, json.dumps({"file": str(path), "config": load(path)}, default=str).encode())
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if not self._authorized() or urlparse(self.path).path != "/api/config":
                return self._send(403, b'{"error":"forbidden"}')
            try:
                length = int(self.headers.get("Content-Length", "0"))
                config = json.loads(self.rfile.read(length))
                if not isinstance(config, dict):
                    raise ValueError("config must be an object")
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, json.dumps({"errors": [str(exc)]}).encode())
            errors = save(path, config)
            self._send(422 if errors else 200, json.dumps({"errors": errors, "saved": not errors}).encode())

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, help="default: hosts/<hostname>/config.yml, else config.captured.yml")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    path = args.file or default_file()
    if not path.exists():
        raise SystemExit(f"{path} does not exist")
    token = secrets.token_urlsafe(16)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(path, token, args.port))
    url = f"http://127.0.0.1:{args.port}/?token={token}"
    print(f"editing {path}\n{url}\n(Ctrl-C to stop)", file=sys.stderr)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Config Editor</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#dcdcd6;--acc:#2456c9;--bad:#b3261e;--ok:#1b7a3a}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--mut:#9a9a94;--card:#212120;--line:#3a3a37;--acc:#7aa2ff;--bad:#ff8a80;--ok:#6fd08c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0}header .file{color:var(--mut);font-size:12px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
main{padding:16px;max-width:1100px;margin:0 auto}
section{margin-bottom:24px}h2{font-size:15px;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin-bottom:10px}
.card h3{margin:0 0 8px;font-size:14px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.card h3 small{color:var(--mut);font-weight:400}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px 12px}
label{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:2px}
label.chk{flex-direction:row;align-items:center;gap:6px;color:var(--fg);font-size:13px}
input,select{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:5px;padding:5px 7px;min-width:0}
input:focus,select:focus{outline:2px solid var(--acc);outline-offset:-1px}
fieldset{border:1px dashed var(--line);border-radius:6px;margin:10px 0 0;padding:8px}legend{font-size:12px;color:var(--mut);padding:0 4px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:6px 12px;cursor:pointer}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}button:disabled{opacity:.5;cursor:default}
.ro{color:var(--mut);font-size:12px}.pill{font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid var(--line)}
.up{color:var(--ok)}#msg{font-size:13px}#msg.bad{color:var(--bad)}#msg.ok{color:var(--ok)}
ul.errs{margin:6px 0 0;padding-left:18px;color:var(--bad)}
details summary{cursor:pointer;color:var(--mut)}pre{overflow:auto;font-size:12px}
</style></head><body>
<header><h1>Config Editor</h1><span class="file" id="file"></span><span id="msg"></span>
<button class="primary" id="save">Save</button></header>
<main>
<section><h2>NICs</h2><div id="nics"></div></section>
<section><h2>Cameras</h2><div id="cams"></div><button id="addcam">+ Add camera</button></section>
<section><details><summary>System, GPUs, storage (read-only)</summary><pre id="sys"></pre></details></section>
<ul class="errs" id="errs"></ul>
</main>
<script>
const token = new URLSearchParams(location.search).get('token');
const H = {'X-Token': token, 'Content-Type': 'application/json'};
const ROLES = ['camera','spare','management','uplink','unknown'];
const FIBER = ['', 'SMF','MMF','DAC'];
let cfg = {}, nics = [], cams = [];

const el = (t, a={}, ...kids) => { const e = document.createElement(t);
  for (const [k,v] of Object.entries(a)) k==='class' ? e.className=v : k.startsWith('on') ? e.addEventListener(k.slice(2),v) : e.setAttribute(k,v);
  kids.flat().forEach(c => e.append(c)); return e; };
const get = (o, p) => p.split('.').reduce((x,k) => x==null ? x : x[k], o);
const set = (o, p, v) => { const ks=p.split('.'); const last=ks.pop(); let x=o; for (const k of ks) x = x[k] ??= {}; x[last]=v; };

function field(obj, path, label, {type='text', options=null, num=false}={}) {
  const cur = get(obj, path);
  let inp;
  if (options) {
    inp = el('select', {}, options.map(o => el('option', {value:o}, o || '—')));
    inp.value = cur ?? '';
  } else if (type === 'checkbox') {
    inp = el('input', {type:'checkbox'}); inp.checked = !!cur;
    inp.addEventListener('change', () => set(obj, path, inp.checked));
    return el('label', {class:'chk'}, inp, label);
  } else {
    inp = el('input', {type:'text', value: cur ?? ''});
  }
  inp.addEventListener('input', () => {
    let v = inp.value.trim();
    if (v === '') v = null; else if (num && !isNaN(Number(v))) v = Number(v);
    set(obj, path, v);
  });
  return el('label', {}, label, inp);
}

function nicCard(n) {
  return el('div', {class:'card'},
    el('h3', {}, n.name, el('small', {}, [n.model, n.pcie_id].filter(Boolean).join(' · ')),
       el('span', {class:'pill'}, n.driver || ''), el('span', {class:'ro'}, n.mac_address || '')),
    el('div', {class:'grid'},
      field(n, 'role', 'Role', {options: ROLES}),
      field(n, 'ip_address', 'IP / CIDR'),
      field(n, 'mtu', 'MTU', {num:true}),
      field(n, 'link_settings.speed', 'Link speed (Mb/s)', {num:true}),
      field(n, 'part_number', 'NIC part number'),
      field(n, 'serial_number', 'NIC serial number'),
      field(n, 'managed', 'Managed by scripts', {type:'checkbox'}),
      field(n, 'expected_link', 'Link expected up', {type:'checkbox'}),
      field(n, 'link_settings.autoneg', 'Autoneg', {type:'checkbox'})),
    el('fieldset', {}, el('legend', {}, 'Transceiver / cable'),
      el('div', {class:'grid'},
        field(n, 'transceiver.brand', 'Brand'), field(n, 'transceiver.model', 'Model'),
        field(n, 'transceiver.serial_number', 'Serial'),
        field(n, 'transceiver.speed', 'Speed (Gb/s)', {num:true}),
        field(n, 'transceiver.wavelength', 'Wavelength (nm)', {num:true}),
        field(n, 'transceiver.max_distance', 'Max distance (m)', {num:true}),
        field(n, 'transceiver.fiber_type', 'Fiber type', {options: FIBER}))));
}

function camCard(c, i) {
  const portNames = ['', ...nics.map(n => n.name)];
  return el('div', {class:'card'},
    el('h3', {}, c.mac || 'new camera', el('small', {}, c.model || ''),
       el('button', {onclick: () => { cams.splice(i,1); render(); }}, 'Remove')),
    el('div', {class:'grid'},
      field(c, 'mac', 'MAC address (key, e.g. E0-55-97-1E-AB-ED)'),
      field(c, 'model', 'Camera model'),
      field(c, 'serial_number', 'Serial number', {num:true}),
      field(c, 'ip_address', 'IP address'),
      field(c, 'nic_port', 'NIC port', {options: portNames}),
      field(c, 'interface', 'Interface')),
    el('fieldset', {}, el('legend', {}, 'Lens'),
      el('div', {class:'grid'},
        field(c, 'lens.manufacturer', 'Manufacturer'), field(c, 'lens.model', 'Model'),
        field(c, 'lens.serial_number', 'Serial'), field(c, 'lens.mount', 'Mount'),
        field(c, 'lens.focal_length_mm', 'Focal length (mm)', {num:true}),
        field(c, 'lens.max_aperture_f', 'Max aperture (f/)', {num:true}))));
}

function render() {
  document.getElementById('nics').replaceChildren(...nics.map(nicCard));
  document.getElementById('cams').replaceChildren(...cams.map(camCard));
  const {nics: _n, cameras: _c, ...rest} = cfg;
  document.getElementById('sys').textContent = JSON.stringify(rest, null, 1);
}

function collect() {
  const out = {...cfg};
  out.nics = nics.map(({name, ...rest}) => ({[name]: rest}));
  out.cameras = Object.fromEntries(cams.map(({mac, ...rest}) => [mac, rest]));
  return out;
}

async function load() {
  const r = await fetch('/api/config', {headers: H});
  if (!r.ok) { document.getElementById('msg').textContent = 'Not authorized: open the URL printed in the terminal.'; return; }
  const d = await r.json();
  cfg = d.config; document.getElementById('file').textContent = d.file;
  nics = (cfg.nics || []).map(item => { const [name, v] = Object.entries(item)[0]; return {name, ...v}; });
  cams = Object.entries(cfg.cameras || {}).map(([mac, v]) => ({mac, ...v}));
  render();
}

document.getElementById('addcam').onclick = () => { cams.push({mac:'', lens:{}}); render(); };
document.getElementById('save').onclick = async () => {
  const msg = document.getElementById('msg'), errs = document.getElementById('errs');
  errs.replaceChildren(); msg.className=''; msg.textContent='Saving…';
  const r = await fetch('/api/config', {method:'POST', headers:H, body: JSON.stringify(collect())});
  const d = await r.json();
  if (d.saved) { msg.className='ok'; msg.textContent='Saved (backup kept)'; cfg = collect(); }
  else { msg.className='bad'; msg.textContent='Not saved'; (d.errors||[]).forEach(e => errs.append(el('li', {}, e))); }
};
load();
</script></body></html>
"""

if __name__ == "__main__":
    sys.exit(main())
