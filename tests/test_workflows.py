from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pages_deployment_retries_after_the_action_timeout() -> None:
    workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")

    assert "timeout-minutes: 25" in workflow
    assert "continue-on-error: true" in workflow
    assert "if: steps.deployment.outcome != 'success'" in workflow
    assert workflow.count("actions/deploy-pages@") == 2
