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

A consumer that also reads another pancakebatter document adds it under
`documents`, keyed by the document's schema name, with that document's stable
keys (`schemas/<name>.v<N>.stable_keys.json`):

```json
{
  "consumer": "orange",
  "repo": "orange",
  "schema_version": 1,
  "keys": ["cameras.*.serial_number", "rigs.*.rig_id"],
  "documents": {
    "rig_config": {
      "schema_version": 1,
      "keys": ["rig_id", "rig_geometry_revision", "cameras.*.viewing_side",
               "cameras.*.optical_mirror_count", "cameras.*.reverse_x",
               "cameras.*.reverse_y", "cameras.*.rotation_deg",
               "cameras.*.derived.recorded_space_parity"]
    }
  }
}
```

The same test checks those keys against that document's stable list and every
`rigs/*/rig.yml`. (The example is how Orange would declare itself; Orange has no
consumer file yet.)
