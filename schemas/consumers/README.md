# Consumers of the host configuration

One file per program that reads the installed host configuration
(`/etc/pancakebatter/host.yml`, see `docs/host_config_interface.md`). It states
which schema version the program targets and which key paths it reads.
`tests/test_consumers.py` fails if a declared key is not on the stable list
(`schemas/system_config.v1.stable_keys.json`) or does not resolve in a host
config that has the section, so a rename that would break a consumer fails
here first.

```json
{
  "consumer": "citrus",
  "repo": "citrus",
  "schema_version": 1,
  "keys": ["system_info.hostname", "cameras.*.serial_number"],
  "notes": "optional free text"
}
```

Key path syntax: dotted; `*` matches every element of a list or every value of
a map. NIC entries are one-key maps, so NIC fields are `nics.*.*.<field>`.
