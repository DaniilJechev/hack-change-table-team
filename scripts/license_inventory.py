"""Refresh declared license metadata for locked dependencies (network required).

Metadata inventory, not a legal compatibility verdict or a binary distribution SBOM.
"""

import concurrent.futures
import io
import json
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AI-buddy-license-inventory/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def python_entry(package):
    name, version = package["name"], package["version"]
    if name == "torch" and "+cpu" in version:
        # CPU wheels live on download.pytorch.org, not on PyPI with that local version.
        release = version.split("+", 1)[0]
        url = f"https://raw.githubusercontent.com/pytorch/pytorch/v{release}/LICENSE"
        row = {"ecosystem": "python", "name": name, "version": version, "source": url}
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read()
            path = ROOT / "third_party/python" / f"torch-{release}-LICENSE.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            row.update(
                license="SEE_LICENSE_FILES",
                license_files=[path.relative_to(ROOT).as_posix()],
                note=("Upstream release license; CPU wheel contents and embedded licenses not inspected."),
            )
        except OSError as error:
            row.update(license="UNKNOWN", error=str(error))
        return row
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    row = {"ecosystem": "python", "name": name, "version": version, "source": url}
    try:
        info = get_json(url)["info"]
        classifiers = [c for c in info.get("classifiers", []) if c.startswith("License ::")]
        row["license"] = info.get("license_expression") or info.get("license") or "UNKNOWN"
        if row["license"] == "UNKNOWN" and classifiers:
            row["license"] = "DECLARED_CLASSIFIERS: " + "; ".join(classifiers)
        row["classifiers"] = classifiers
        row["license_files"] = info.get("license_files") or []
        row["project_urls"] = info.get("project_urls") or {}
    except (OSError, ValueError, KeyError) as error:
        row.update(license="UNKNOWN", error=str(error))
    return row


def go_entry(module):
    name, version = module
    escaped = "".join("!" + c.lower() if c.isupper() else c for c in name)
    url = f"https://proxy.golang.org/{escaped}/@v/{version}.zip"
    row = {
        "ecosystem": "go",
        "name": name,
        "version": version,
        "source": url,
        "license": "SEE_LICENSE_FILES",
        "license_files": [],
    }
    try:
        with urllib.request.urlopen(url, timeout=45) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        prefix = f"{name}@{version}/"
        for member in archive.namelist():
            relative = member.removeprefix(prefix)
            if "/" not in relative and relative.lower().startswith(("license", "copying", "notice")):
                path = ROOT / "third_party/go" / name / version / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(member))
                row["license_files"].append(path.relative_to(ROOT).as_posix())
        if not row["license_files"]:
            row["license"] = "UNKNOWN"
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        row.update(license="UNKNOWN", error=str(error))
    return row


def main():
    lock = tomllib.loads((ROOT / "agent/uv.lock").read_text(encoding="utf-8"))
    packages = [p for p in lock["package"] if "editable" not in p.get("source", {})]
    # Optional H4 service has its own pinned Python environment.
    for line in (ROOT / "mcp/powerpoint/requirements.txt").read_text().splitlines():
        match = re.fullmatch(r"([\w-]+)==([\w.+-]+)", line.strip())
        if match and not any(p["name"] == match[1] and p["version"] == match[2] for p in packages):
            packages.append({"name": match[1], "version": match[2]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(python_entry, packages))
    for path in [ROOT / "desktop/package-lock.json", ROOT / "mcp/google-calendar/package-lock.json"]:
        lock = json.loads(path.read_text())
        for location, package in lock["packages"].items():
            if not location:
                continue
            rows.append(
                {
                    "ecosystem": "npm",
                    "manifest": path.relative_to(ROOT).as_posix(),
                    "name": package.get("name") or location.split("node_modules/")[-1],
                    "version": package.get("version"),
                    "license": package.get("license", "UNKNOWN"),
                    "dev": package.get("dev", False),
                    "optional": package.get("optional", False),
                    "source": package.get("resolved", ""),
                }
            )
    modules = re.findall(r"^\s*([\w.\-/]+)\s+(v\S+)", (ROOT / "backend/go.mod").read_text(), re.M)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        rows.extend(pool.map(go_entry, modules))
    rows.sort(key=lambda r: (r["ecosystem"], r["name"], r.get("version") or "", r.get("manifest", "")))
    output = {
        "schema_version": 1,
        "scope": "Locked declarations including optional/dev/platform variants; not installed-only",
        "limitations": (
            "Registry metadata may be incomplete; "
            "preserve actual license/NOTICE files in redistributed artifacts."
        ),
        "dependencies": rows,
    }
    target = ROOT / "third_party/dependency-licenses.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from collections import Counter

    print(dict(Counter(row["ecosystem"] for row in rows)))
    print("Missing declarations:", [(r["name"], r["version"]) for r in rows if r["license"] == "UNKNOWN"])


if __name__ == "__main__":
    main()
