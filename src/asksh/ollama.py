from typing import Any

import requests


def verify_ollama_status(
    required_model: str | None = None,
    base_url: str = "http://localhost:11434",
) -> dict[str, Any]:
    """
    Verifies if Ollama is running and checks if a required model is pulled.

    Args:
        required_model: The name of the model to check (e.g., 'llama3.2:3b').
        base_url: The address of the Ollama server.

    Returns:
        A dictionary containing the state and version info if successful.
    """
    base_url = base_url.rstrip("/")

    # 1. Check Server Connection
    try:
        version_url = f"{base_url}/api/version"
        response = requests.get(version_url, timeout=4.0)
        response.raise_for_status()
        server_version = response.json().get("version", "Unknown")

    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    ) as network_err:
        raise RuntimeError(
            f"❌ [OLLAMA_OFFLINE]: Cannot connect to Ollama at '{base_url}'.\n"
            f"👉 Troubleshooting (Start the background service):\n"
            f"   - Linux: Run 'sudo systemctl start ollama' to launch it as a system daemon.\n"
            f"   - Mac: Open the 'Ollama' app from Applications, or run: brew services start ollama\n"
            f"   - Windows: Click the Ollama tray icon, or run: start /B ollama serve\n"
            f"   - Details: {network_err}"
        ) from network_err

    except requests.exceptions.HTTPError as http_err:
        raise RuntimeError(
            f"❌ [OLLAMA_API_ERROR]: Server responded with an error code: {http_err.response.status_code}.\n"
            f"   Details: {http_err.response.text}"
        ) from http_err

    # 2. Check Model Availability
    if required_model:
        try:
            tags_url = f"{base_url}/api/tags"
            tags_response = requests.get(tags_url, timeout=4.0)
            tags_response.raise_for_status()

            local_models: list[dict[str, Any]] = tags_response.json().get("models", [])
            installed_names = [m["name"] for m in local_models]

            has_model = (
                required_model in installed_names
                or f"{required_model}:latest" in installed_names
            )

            if not has_model:
                raise RuntimeError(
                    f"❌ [OLLAMA_MODEL_MISSING]: The server is running, but the model '{required_model}' was not found.\n"
                    f"👉 Troubleshooting:\n"
                    f"   - Run 'ollama pull {required_model}' in your terminal to download it.\n"
                    f"   - Available local models found: {installed_names if installed_names else 'None'}"
                )

        except requests.exceptions.RequestException as err:
            raise RuntimeError(
                f"❌ [OLLAMA_TAGS_FETCH_FAILED]: Successfully pinged server, but failed fetching model lists.\n"
                f"   Details: {err}"
            ) from err

    return {
        "status": "healthy",
        "ollama_version": server_version,
        "base_url": base_url,
        "verified_model": required_model,
    }
