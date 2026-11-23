import importlib.util
import os

if "history_cleaner" not in value.data:
    spec = importlib.util.spec_from_file_location("verified_history_rewrite", os.environ["ZVG_REWRITE_HELPER"])
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    value.data["history_helper"] = helper
    value.data["history_cleaner"] = helper.cleaner_from_environment()
    value.data["json_rewrites"] = {}
cleaner = value.data["history_cleaner"]
helper = value.data["history_helper"]
if cleaner.withdrawn_path(filename) or (isinstance(blob_id, bytes) and blob_id.decode("ascii") in cleaner.blob_ids):
    return (None, mode, blob_id)
if mode == b"160000":
    return (filename, mode, blob_id)
if filename.lower().endswith(b".json"):
    cache = value.data["json_rewrites"]
    if blob_id not in cache:
        raw = value.get_contents_by_identifier(blob_id)
        old_sha = helper.blob_sha(raw)
        new_contents, result = cleaner.transform_json(old_sha, raw)
        cache[blob_id] = value.insert_file_with_contents(new_contents) if result["changed"] else blob_id
    return (filename, mode, cache[blob_id])
if filename.lower().endswith(helper.TEXT_SUFFIXES):
    cleaner.inspect_text(filename, value.get_contents_by_identifier(blob_id))
return (filename, mode, blob_id)
