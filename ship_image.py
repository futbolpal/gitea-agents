#!/usr/bin/env python3
"""Script to build, tag, and push kilo-agents Docker image using podman."""

import subprocess
import sys

def main():
    """Build, tag, and push the kilo-agents image."""
    image_name = "kilo-agents"
    tag = "latest"
    registry = "homenas.tail38254.ts.net:5001"

    try:
        print("Building Docker image...")
        subprocess.run([
            "podman", "build", "--platform", "linux/arm64/v8", "-t", f"{image_name}:{tag}", "."
        ], check=True)

        print("Tagging image for registry...")
        full_tag = f"{registry}/{image_name}:{tag}"
        subprocess.run([
            "podman", "tag", f"{image_name}:{tag}", full_tag
        ], check=True)

        print("Pushing image to registry...")
        subprocess.run([
            "podman", "push", full_tag
        ], check=True)

        print(f"Successfully built, tagged, and pushed {full_tag}")

    except subprocess.CalledProcessError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Operation cancelled", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()