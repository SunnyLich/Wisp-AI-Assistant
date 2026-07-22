import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / ".github" / "README.md"
REQUIREMENTS_DOC = ROOT / "docs" / "DEPENDENCY_LOCKS.md"


class DocsSetupGuidanceTests(unittest.TestCase):
    def test_readme_does_not_recommend_generic_venv_creation(self) -> None:
        readme = README.read_text(encoding="utf-8")

        self.assertNotIn("python -m venv .venv", readme)

    def test_developer_readme_points_to_preflight_after_setup(self) -> None:
        readme = README.read_text(encoding="utf-8")
        developer_readme = (ROOT / "docs" / "DEVELOPER_README.md").read_text(encoding="utf-8")

        self.assertIn("../docs/DEVELOPER_README.md", readme)
        self.assertIn(r".\.venv\Scripts\python.exe scripts\check_dev_environment.py", developer_readme)
        self.assertIn(".venv/bin/python scripts/check_dev_environment.py", developer_readme)

    def test_readme_guides_normal_configuration_through_settings(self) -> None:
        readme = README.read_text(encoding="utf-8")

        self.assertIn("Use the Settings window for normal setup", readme)
        self.assertNotIn("cp .env.example .env", readme)
        self.assertNotIn("Copy-Item .env.example .env", readme)

    def test_dependency_docs_cover_all_requirement_manifests(self) -> None:
        docs = REQUIREMENTS_DOC.read_text(encoding="utf-8")

        self.assertIn("`requirements/requirements.txt`", docs)
        self.assertIn("`requirements/requirements-dev.txt`", docs)
        self.assertIn("`requirements/requirements-build.txt`", docs)
        self.assertIn("`requirements/requirements-macos.lock`", docs)
        self.assertIn("PyInstaller", docs)

    def test_build_docs_release_tag_matches_project_version(self) -> None:
        build_docs = (ROOT / "docs" / "BUILDING_EXE.md").read_text(encoding="utf-8")
        with (ROOT / "pyproject.toml").open("rb") as handle:
            version = tomllib.load(handle)["project"]["version"]

        self.assertIn(f"git tag v{version}", build_docs)
        self.assertIn(f"git push origin v{version}", build_docs)
        self.assertIn("Tags without the `v` prefix do not trigger release builds.", build_docs)

    def test_release_workflow_requires_v_prefixed_version_tags(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

        self.assertIn('      - "v*"', workflow)
        self.assertNotIn('      - "[0-9]*.[0-9]*.[0-9]*"', workflow)
        self.assertIn('expected_tag="v${project_version}"', workflow)
        self.assertIn("Release tag mismatch", workflow)

    def test_architecture_docs_do_not_reference_removed_paths(self) -> None:
        docs = "\n".join(
            [
                (ROOT / "docs" / "DEVELOPER_README.md").read_text(encoding="utf-8"),
                (ROOT / "docs" / "OVERVIEW.md").read_text(encoding="utf-8"),
            ]
        )

        self.assertNotIn("`core.llm`", docs)
        self.assertNotIn("`experiments/`", docs)


if __name__ == "__main__":
    unittest.main()
