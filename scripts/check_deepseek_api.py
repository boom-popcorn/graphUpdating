from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

from odkg.demo import project_root
from odkg.environment import configure_model_environment


def main() -> None:
    configure_model_environment(project_root())
    parser = argparse.ArgumentParser(
        description="Check DeepSeek authentication and list available models."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ODKG_LLM_BASE_URL", "https://api.deepseek.com"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("ODKG_LLM_MODEL", "deepseek-flash"),
    )
    args = parser.parse_args()

    api_key = os.environ.get("ODKG_LLM_API_KEY") or os.environ.get(
        "DEEPSEEK_API_KEY"
    )
    if not api_key:
        raise SystemExit(
            "No API key found. Run integrations/deepseek/configure-session.ps1 first."
        )

    endpoint = args.base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"DeepSeek returned HTTP {exc.code}; check the key/account.")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"DeepSeek connection failed: {type(exc).__name__}: {exc}")

    models = [item.get("id") for item in payload.get("data", [])]
    print(
        json.dumps(
            {
                "endpoint": endpoint,
                "authenticated": True,
                "configured_model": args.model,
                "configured_model_available": args.model in models,
                "available_models": models,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

