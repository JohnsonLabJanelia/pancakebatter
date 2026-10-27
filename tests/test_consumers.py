"""The host config as an interface: stable keys resolve, consumers only use stable keys."""
import json
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
STABLE = json.loads((REPO / "schemas" / "system_config.v1.stable_keys.json").read_text())
SCHEMA = json.loads((REPO / "schemas" / "system_config.v1.schema.json").read_text())
CONSUMERS = sorted((REPO / "schemas" / "consumers").glob("*.json"))
HOST_CONFIGS = {p.parent.name: yaml.safe_load(p.read_text()) for p in sorted((REPO / "hosts").glob("*/config.yml"))}
# Other documents a consumer may declare under "documents": schema name -> (stable keys, instances).
DOCUMENTS = {
    "rig_config": (json.loads((REPO / "schemas" / "rig_config.v1.stable_keys.json").read_text()),
                   {p.parent.name: yaml.safe_load(p.read_text()) for p in sorted((REPO / "rigs").glob("*/rig.yml"))}),
}


def resolve(doc, path):
    """All values at a dotted path; '*' fans out over list elements or map values. Returns (found, values)."""
    values = [doc]
    for part in path.split("."):
        nxt = []
        for v in values:
            if part == "*":
                if isinstance(v, dict):
                    nxt.extend(v.values())
                elif isinstance(v, list):
                    nxt.extend(v)
            elif isinstance(v, dict) and part in v:
                nxt.append(v[part])
        values = nxt
        if not values:
            return False, []
    return True, values


def schema_declares(schema, path):
    """True when the JSON Schema declares a dotted key path ('*' = array items or map values)."""
    def nodes(node):
        if "$ref" in node:  # local refs only: #/$defs/<name>
            node = {**SCHEMA["$defs"][node["$ref"].split("/")[-1]], **{k: v for k, v in node.items() if k != "$ref"}}
        yield node
        for branch in node.get("allOf", []) + node.get("anyOf", []) + node.get("oneOf", []):
            yield from nodes(branch)

    current = [schema]
    for part in path.split("."):
        nxt = []
        for node in (n for c in current for n in nodes(c)):
            if part == "*":
                nxt += [s for s in (node.get("items"), node.get("additionalProperties")) if isinstance(s, dict)]
                nxt += list(node.get("patternProperties", {}).values())
            elif part in node.get("properties", {}):
                nxt.append(node["properties"][part])
        if not nxt:
            return False
        current = nxt
    return True


class StableKeyTests(unittest.TestCase):
    def test_required_stable_keys_resolve_on_every_host_that_has_the_section(self):
        for host, cfg in HOST_CONFIGS.items():
            for key in STABLE["keys"]:
                section = key.split(".")[0]
                if section in STABLE["optional_sections"] and section not in cfg:
                    continue
                if key in STABLE["optional_keys"]:
                    continue
                with self.subTest(host=host, key=key):
                    self.assertTrue(resolve(cfg, key)[0], f"{key} missing on {host}")

    def test_optional_stable_keys_are_a_subset_and_each_resolves_somewhere_or_is_new(self):
        self.assertTrue(set(STABLE["optional_keys"]) <= set(STABLE["keys"]))
        # "New" = declared in the schema but on no host yet (e.g. daqs before any DAQ is installed).
        never = [k for k in STABLE["optional_keys"]
                 if not any(resolve(c, k)[0] for c in HOST_CONFIGS.values()) and not schema_declares(SCHEMA, k)]
        self.assertEqual(never, [], never)

    def test_every_stable_key_is_declared_in_the_schema_or_resolves_on_a_host(self):
        missing = [k for k in STABLE["keys"]
                   if not schema_declares(SCHEMA, k) and not any(resolve(c, k)[0] for c in HOST_CONFIGS.values())]
        self.assertEqual(missing, [], missing)

    def test_schema_declares_follows_refs_and_rejects_typos(self):
        self.assertTrue(schema_declares(SCHEMA, "daqs.*.channels.*.source.kind"))
        self.assertTrue(schema_declares(SCHEMA, "photodetectors.*.calibration"))
        self.assertFalse(schema_declares(SCHEMA, "daqs.*.chanels.*.id"))

    def test_stable_key_list_is_well_formed(self):
        self.assertEqual(STABLE["schema"], {"name": "system_config", "version": 1})
        self.assertEqual(len(STABLE["keys"]), len(set(STABLE["keys"])))


class ConsumerTests(unittest.TestCase):
    def test_there_is_at_least_one_consumer_file(self):
        self.assertTrue(CONSUMERS)

    def test_consumers_declare_only_stable_keys_of_the_current_version(self):
        for path in CONSUMERS:
            decl = json.loads(path.read_text())
            with self.subTest(consumer=path.name):
                for field in ("consumer", "repo", "schema_version", "keys"):
                    self.assertIn(field, decl)
                self.assertEqual(decl["schema_version"], STABLE["schema"]["version"])
                self.assertTrue(decl["keys"])
                unstable = sorted(set(decl["keys"]) - set(STABLE["keys"]))
                self.assertEqual(unstable, [], f"{path.name} relies on keys that are not promised: {unstable}")

    def test_consumer_keys_resolve_where_the_section_exists(self):
        for path in CONSUMERS:
            decl = json.loads(path.read_text())
            for host, cfg in HOST_CONFIGS.items():
                for key in decl["keys"]:
                    section = key.split(".")[0]
                    if (section in STABLE["optional_sections"] and section not in cfg) or key in STABLE["optional_keys"]:
                        continue
                    with self.subTest(consumer=path.name, host=host, key=key):
                        self.assertTrue(resolve(cfg, key)[0])


class DocumentConsumerTests(unittest.TestCase):
    def test_document_declarations_use_stable_keys_and_resolve_in_every_instance(self):
        for path in CONSUMERS:
            for name, decl in json.loads(path.read_text()).get("documents", {}).items():
                with self.subTest(consumer=path.name, document=name):
                    self.assertIn(name, DOCUMENTS, f"{path.name} declares unknown document {name}")
                    stable, instances = DOCUMENTS[name]
                    self.assertEqual(decl["schema_version"], stable["schema"]["version"])
                    self.assertTrue(decl["keys"])
                    unstable = sorted(set(decl["keys"]) - set(stable["keys"]))
                    self.assertEqual(unstable, [], f"{path.name} relies on {name} keys that are not promised: {unstable}")
                    for inst, doc in instances.items():
                        for key in decl["keys"]:
                            if key not in stable["optional_keys"]:
                                self.assertTrue(resolve(doc, key)[0], f"{key} missing in {name} {inst}")

    def test_document_stable_keys_resolve_in_every_instance(self):
        for name, (stable, instances) in DOCUMENTS.items():
            self.assertTrue(instances, f"no {name} documents")
            for inst, doc in instances.items():
                for key in set(stable["keys"]) - set(stable["optional_keys"]):
                    with self.subTest(document=name, instance=inst, key=key):
                        self.assertTrue(resolve(doc, key)[0])


class ResolveHelperTests(unittest.TestCase):
    def test_star_fans_out_over_lists_and_maps(self):
        doc = {"nics": [{"a": {"role": "camera"}}, {"b": {"role": "spare"}}], "cameras": {"m1": {"serial_number": "1"}}}
        self.assertEqual(resolve(doc, "nics.*.*.role"), (True, ["camera", "spare"]))
        self.assertEqual(resolve(doc, "cameras.*.serial_number"), (True, ["1"]))
        self.assertEqual(resolve(doc, "cameras.*.lens"), (False, []))


if __name__ == "__main__":
    unittest.main()
