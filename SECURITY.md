# Security

Framewire runs locally and accepts URLs you provide. Keep camera and webhook credentials in environment variables or a protected local configuration outside the repository. `--webhook-token-env` avoids putting a bearer token in the command line. Source labels retain only URL scheme, host and port; OpenCV or the operating system may still produce their own diagnostics.

An installed adapter is executable Python with your account's permissions. `framewire adapters add` copies a bundled example into your folder, while `framewire adapters new` creates local code for you to edit. Do not install untrusted adapter files. `--no-adapters` runs the camera core without them. `--dry-run` disables only the built-in servo and GPIO path; custom adapters must implement their own dry-run behavior. Voice transcripts and command labels appear in stdout and configured webhooks; raw audio and video do not.

Do not rely on camera recognition or this process for physical safety. GPIO is a logic signal; hazardous loads require an independent interlock and appropriate driver. A lost video source releases configured outputs, but abrupt power loss or forced termination cannot run cleanup code.

Please report security issues privately through GitHub's repository security advisory flow if available, or contact the maintainer privately. Do not include a live camera URL, password or token in a public issue.
