"""Runtime-discoverable out-of-process extension bundles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from typing import Any, Literal
import zipfile

import httpx
import yaml

from src.skills import SkillDiscoveryRoot


ExtensionInstallScope = Literal["repo", "user"]


@dataclass(frozen=True)
class ExtensionToolSpec:
    """One tool definition exported by an extension bundle."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ExtensionSpec:
    """A validated extension bundle manifest."""

    name: str
    version: str
    description: str
    root_dir: Path
    manifest_file: Path
    command: tuple[str, ...]
    tool_specs: tuple[ExtensionToolSpec, ...]
    install_scope: ExtensionInstallScope
    skill_root: Path | None = None


@dataclass(frozen=True)
class ExtensionCatalogPackage:
    """A curated installable package entry."""

    catalog_name: str
    name: str
    version: str
    archive_url: str
    sha256: str
    bundle_root: str
    description: str = ""


@dataclass(frozen=True)
class ExtensionInstallResult:
    """The result of installing one curated extension package."""

    extension: ExtensionSpec
    package: ExtensionCatalogPackage
    install_path: Path


class ExtensionManager:
    """Discover and install runtime extension bundles."""

    def __init__(
        self,
        *,
        repo_root: Path,
        runtime_config,
        user_root: Path | None = None,
        repo_extensions_root: Path | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runtime_config = runtime_config
        configured_repo_root = repo_extensions_root or Path(runtime_config.extensions.repo_root)
        configured_user_root = user_root or Path(runtime_config.extensions.user_root)
        self.repo_extensions_root = self._resolve_repo_root(configured_repo_root)
        self.user_root = configured_user_root.expanduser().resolve()
        self._extensions: dict[str, ExtensionSpec] = {}
        self._warnings: list[str] = []
        self._catalog_warnings: list[str] = []
        self._catalog_packages_cache: list[ExtensionCatalogPackage] | None = None

    def discover(self) -> list[str]:
        """Discover extension bundles under the configured roots."""
        if not self.runtime_config.extensions.enabled:
            self._extensions = {}
            self._warnings = []
            self._catalog_warnings = []
            self._catalog_packages_cache = None
            return []

        discovered: dict[str, ExtensionSpec] = {}
        warnings: list[str] = []
        discovery_roots: list[tuple[ExtensionInstallScope, Path]] = [
            ("user", self.user_root),
            ("repo", self.repo_extensions_root),
        ]

        for scope, root in discovery_roots:
            if not root.exists():
                continue

            for manifest_file in sorted(root.rglob("EXTENSION.yaml")):
                spec, manifest_warnings = self._load_manifest(manifest_file, install_scope=scope)
                warnings.extend(manifest_warnings)
                if spec is None:
                    continue
                previous = discovered.get(spec.name)
                if previous is not None:
                    warnings.append(
                        f"Duplicate extension '{spec.name}': {spec.manifest_file} overrides {previous.manifest_file}"
                    )
                discovered[spec.name] = spec

        self._extensions = discovered
        self._warnings = warnings
        return list(warnings)

    def list_extensions(self) -> list[ExtensionSpec]:
        """Return discovered extensions."""
        return sorted(self._extensions.values(), key=lambda item: item.name)

    def get_extension(self, name: str) -> ExtensionSpec | None:
        """Return one extension by name."""
        return self._extensions.get(name)

    def get_tool_specs(self) -> list[tuple[ExtensionSpec, ExtensionToolSpec]]:
        """Return all discovered extension tools with provenance."""
        tool_defs: list[tuple[ExtensionSpec, ExtensionToolSpec]] = []
        for extension in self.list_extensions():
            for tool_spec in extension.tool_specs:
                tool_defs.append((extension, tool_spec))
        return tool_defs

    def get_skill_roots(self) -> list[SkillDiscoveryRoot]:
        """Return extension skill roots for skill discovery."""
        roots: list[SkillDiscoveryRoot] = []
        for extension in self.list_extensions():
            if extension.skill_root is None or not extension.skill_root.exists():
                continue
            roots.append(
                SkillDiscoveryRoot(
                    source="extension",
                    root=extension.skill_root,
                    extension_name=extension.name,
                    extension_version=extension.version,
                    extension_install_scope=extension.install_scope,
                )
            )
        return roots

    def get_warnings(self) -> list[str]:
        """Return warnings from the last discovery run."""
        return [*self._warnings, *self._catalog_warnings]

    def list_catalog_packages(self) -> list[ExtensionCatalogPackage]:
        """Return read-only cached packages from enabled extension catalogs."""
        if self._catalog_packages_cache is not None:
            return list(self._catalog_packages_cache)

        packages: list[ExtensionCatalogPackage] = []
        warnings: list[str] = []
        for catalog_config in self.runtime_config.extensions.catalogs:
            if not catalog_config.enabled:
                continue
            try:
                response = httpx.get(
                    catalog_config.url,
                    follow_redirects=True,
                    timeout=self.runtime_config.extensions.install_timeout_seconds,
                )
                response.raise_for_status()
                payload = self._parse_catalog_payload(response.text)
                raw_packages = payload.get("packages")
                if not isinstance(raw_packages, list):
                    raise ValueError("catalog is missing a packages list")
                for package_payload in raw_packages:
                    packages.append(
                        self._parse_catalog_package(catalog_config.name, package_payload)
                    )
            except Exception as exc:
                warnings.append(
                    f"Failed to read extension catalog '{catalog_config.name}': {exc}"
                )

        self._catalog_packages_cache = sorted(
            packages,
            key=lambda item: (item.catalog_name, item.name),
        )
        self._catalog_warnings = warnings
        return list(self._catalog_packages_cache)

    def install_from_catalog(self, package_ref: str) -> ExtensionInstallResult:
        """Download, verify, validate, and install one curated extension package."""
        catalog_name, package_name = self._parse_package_ref(package_ref)
        package = self._load_catalog_package(catalog_name, package_name)
        archive_bytes = self._download_archive(package)
        self._verify_archive_hash(archive_bytes, package.sha256)

        with tempfile.TemporaryDirectory(prefix="nano-claw-extension-install-") as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            extracted_root = temp_dir / "extract"
            extracted_root.mkdir(parents=True, exist_ok=True)
            self._extract_archive(archive_bytes, extracted_root)
            bundle_root = extracted_root / package.bundle_root
            if not bundle_root.exists():
                raise ValueError(
                    f"Catalog package '{catalog_name}:{package_name}' did not contain bundle root '{package.bundle_root}'"
                )
            manifest_file = bundle_root / "EXTENSION.yaml"
            if not manifest_file.exists():
                raise ValueError(f"Extension bundle is missing manifest: {manifest_file}")

            extension, warnings = self._load_manifest(manifest_file, install_scope="user")
            if extension is None:
                warning_text = warnings[0] if warnings else "unknown validation failure"
                raise ValueError(f"Installed extension is invalid: {warning_text}")
            if warnings:
                raise ValueError(warnings[0])
            if extension.name != package.name:
                raise ValueError(
                    f"Catalog package expected extension '{package.name}' but bundle declared '{extension.name}'"
                )

            install_root = self.user_root
            install_root.mkdir(parents=True, exist_ok=True)
            staged_path = install_root / f".{extension.name}.staged"
            target_path = install_root / extension.name
            if staged_path.exists():
                shutil.rmtree(staged_path, ignore_errors=True)
            shutil.copytree(bundle_root, staged_path)
            if target_path.exists():
                shutil.rmtree(target_path)
            os.replace(staged_path, target_path)

        self.discover()
        installed = self.get_extension(package.name)
        if installed is None:
            raise ValueError(f"Installed extension '{package.name}' was not discoverable after install")

        return ExtensionInstallResult(
            extension=installed,
            package=package,
            install_path=installed.root_dir,
        )

    def _resolve_repo_root(self, configured_root: Path) -> Path:
        if configured_root.is_absolute():
            return configured_root.resolve()
        return (self.repo_root / configured_root).resolve()

    def _load_manifest(
        self,
        manifest_file: Path,
        *,
        install_scope: ExtensionInstallScope,
    ) -> tuple[ExtensionSpec | None, list[str]]:
        warnings: list[str] = []
        try:
            raw_text = manifest_file.read_text(encoding="utf-8")
        except Exception as exc:
            return None, [f"Failed to read extension manifest {manifest_file}: {exc}"]

        try:
            payload = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError as exc:
            return None, [f"Skipping invalid extension {manifest_file}: invalid YAML: {exc}"]

        if not isinstance(payload, dict):
            return None, [f"Skipping invalid extension {manifest_file}: manifest must be a mapping"]

        name = payload.get("name")
        version = payload.get("version")
        description = payload.get("description")
        command = payload.get("command")
        tools = payload.get("tools")
        if not isinstance(name, str) or not name.strip():
            return None, [f"Skipping extension {manifest_file}: missing required field 'name'"]
        if not isinstance(version, str) or not version.strip():
            return None, [f"Skipping extension {manifest_file}: missing required field 'version'"]
        if not isinstance(description, str) or not description.strip():
            return None, [f"Skipping extension {manifest_file}: missing required field 'description'"]

        normalized_command = self._normalize_command(command)
        if not normalized_command:
            return None, [f"Skipping extension {manifest_file}: command must be a non-empty string list"]

        if not isinstance(tools, list) or not tools:
            return None, [f"Skipping extension {manifest_file}: tools must be a non-empty list"]

        tool_specs: list[ExtensionToolSpec] = []
        seen_names: set[str] = set()
        for index, tool_payload in enumerate(tools, start=1):
            if not isinstance(tool_payload, dict):
                return None, [f"Skipping extension {manifest_file}: tool #{index} must be a mapping"]
            tool_name = tool_payload.get("name")
            tool_description = tool_payload.get("description")
            parameters = tool_payload.get("parameters")
            if not isinstance(tool_name, str) or not tool_name.strip():
                return None, [f"Skipping extension {manifest_file}: tool #{index} is missing 'name'"]
            if tool_name in seen_names:
                return None, [f"Skipping extension {manifest_file}: duplicate tool name '{tool_name}'"]
            seen_names.add(tool_name)
            if not isinstance(tool_description, str) or not tool_description.strip():
                return None, [f"Skipping extension {manifest_file}: tool '{tool_name}' is missing 'description'"]
            if not isinstance(parameters, dict):
                return None, [f"Skipping extension {manifest_file}: tool '{tool_name}' has invalid parameters schema"]
            if parameters.get("type") != "object":
                return None, [f"Skipping extension {manifest_file}: tool '{tool_name}' parameters must declare type=object"]
            properties = parameters.get("properties", {})
            if not isinstance(properties, dict):
                return None, [f"Skipping extension {manifest_file}: tool '{tool_name}' properties must be a mapping"]
            required = parameters.get("required", [])
            if required is not None and not isinstance(required, list):
                return None, [f"Skipping extension {manifest_file}: tool '{tool_name}' required must be a list"]
            tool_specs.append(
                ExtensionToolSpec(
                    name=tool_name.strip(),
                    description=tool_description.strip(),
                    parameters=parameters,
                )
            )

        root_dir = manifest_file.parent.resolve()
        skill_root = root_dir / "skills"
        return (
            ExtensionSpec(
                name=name.strip(),
                version=version.strip(),
                description=description.strip(),
                root_dir=root_dir,
                manifest_file=manifest_file.resolve(),
                command=tuple(normalized_command),
                tool_specs=tuple(tool_specs),
                install_scope=install_scope,
                skill_root=skill_root.resolve() if skill_root.exists() else None,
            ),
            warnings,
        )

    @staticmethod
    def _normalize_command(value: Any) -> list[str]:
        if isinstance(value, str):
            normalized = value.strip()
            return [normalized] if normalized else []
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    return []
                parts.append(item)
            return parts
        return []

    @staticmethod
    def _parse_package_ref(package_ref: str) -> tuple[str, str]:
        raw_value = (package_ref or "").strip()
        if ":" not in raw_value:
            raise ValueError("Package reference must use <catalog>:<package>")
        catalog_name, package_name = raw_value.split(":", 1)
        if not catalog_name or not package_name:
            raise ValueError("Package reference must use <catalog>:<package>")
        return catalog_name, package_name

    def _load_catalog_package(self, catalog_name: str, package_name: str) -> ExtensionCatalogPackage:
        known_catalog_names = {
            catalog.name
            for catalog in self.runtime_config.extensions.catalogs
            if catalog.enabled
        }
        if catalog_name not in known_catalog_names:
            raise ValueError(f"Unknown extension catalog: {catalog_name}")

        for package in self.list_catalog_packages():
            if package.catalog_name == catalog_name and package.name == package_name:
                return package
        raise ValueError(f"Catalog package not found: {catalog_name}:{package_name}")

    @staticmethod
    def _parse_catalog_payload(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if not stripped:
            raise ValueError("Extension catalog response was empty")
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            payload = yaml.safe_load(stripped) or {}
            if not isinstance(payload, dict):
                raise ValueError("Extension catalog must be a mapping")
            return payload

    @staticmethod
    def _parse_catalog_package(catalog_name: str, payload: Any) -> ExtensionCatalogPackage:
        if not isinstance(payload, dict):
            raise ValueError(f"Extension catalog '{catalog_name}' contains an invalid package entry")
        required_fields = ("name", "version", "archive_url", "sha256", "bundle_root")
        for field_name in required_fields:
            value = payload.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Extension catalog '{catalog_name}' package is missing required field '{field_name}'"
                )
        description = payload.get("description")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValueError(f"Extension catalog '{catalog_name}' package description must be a string")
        return ExtensionCatalogPackage(
            catalog_name=catalog_name,
            name=payload["name"].strip(),
            version=payload["version"].strip(),
            archive_url=payload["archive_url"].strip(),
            sha256=payload["sha256"].strip().lower(),
            bundle_root=payload["bundle_root"].strip(),
            description=description.strip(),
        )

    def _download_archive(self, package: ExtensionCatalogPackage) -> bytes:
        response = httpx.get(
            package.archive_url,
            follow_redirects=True,
            timeout=self.runtime_config.extensions.install_timeout_seconds,
        )
        response.raise_for_status()
        return bytes(response.content)

    @staticmethod
    def _verify_archive_hash(archive_bytes: bytes, expected_sha256: str) -> None:
        actual_sha256 = hashlib.sha256(archive_bytes).hexdigest().lower()
        if actual_sha256 != expected_sha256.lower():
            raise ValueError(
                f"Extension archive hash mismatch: expected {expected_sha256.lower()}, got {actual_sha256}"
            )

    @staticmethod
    def _extract_archive(archive_bytes: bytes, destination: Path) -> None:
        raw_stream = io.BytesIO(archive_bytes)
        if zipfile.is_zipfile(raw_stream):
            raw_stream.seek(0)
            with zipfile.ZipFile(raw_stream) as archive:
                for member in archive.infolist():
                    target = (destination / member.filename).resolve()
                    if not str(target).startswith(str(destination.resolve())):
                        raise ValueError(f"Unsafe extension archive member: {member.filename}")
                archive.extractall(destination)
            return

        raw_stream.seek(0)
        try:
            with tarfile.open(fileobj=raw_stream, mode="r:*") as archive:
                for member in archive.getmembers():
                    target = (destination / member.name).resolve()
                    if not str(target).startswith(str(destination.resolve())):
                        raise ValueError(f"Unsafe extension archive member: {member.name}")
                archive.extractall(destination)
                return
        except tarfile.TarError as exc:
            raise ValueError(f"Unsupported extension archive format: {exc}") from exc
