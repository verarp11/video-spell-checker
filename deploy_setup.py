#!/usr/bin/env python3
"""
Video Spell Checker — GitHub Push Helper
Run this to push your code to GitHub.

Usage:  python3 deploy_setup.py
"""
import os, sys, subprocess, json, getpass, urllib.request, urllib.error

REPO_NAME  = "video-spell-checker"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=SCRIPT_DIR)
    if r.returncode != 0:
        print(f"\n❌ Error: {r.stderr.strip()}"); sys.exit(1)
    return r.stdout.strip()

def main():
    print("\n🎬  Video Spell Checker — GitHub Push")
    print("=" * 42)
    print("\nHow to get a GitHub token:")
    print("  1. Go to  https://github.com/settings/tokens/new")
    print("  2. Note: video-spell-checker")
    print("  3. Tick: ✅ repo")
    print("  4. Click Generate token → copy it\n")

    username = input("GitHub username: ").strip()
    token    = getpass.getpass("GitHub token (hidden): ").strip()

    # Create repo
    print("\n⏳  Creating GitHub repository…")
    data = json.dumps({"name": REPO_NAME, "private": False, "auto_init": False}).encode()
    req  = urllib.request.Request("https://api.github.com/user/repos", data=data,
             headers={"Authorization": f"token {token}", "Content-Type": "application/json", "User-Agent": "setup"})
    try:
        with urllib.request.urlopen(req) as r:
            repo     = json.loads(r.read())
            html_url = repo["html_url"]
            clone    = repo["clone_url"]
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        if "already exists" in str(body.get("errors", "")):
            html_url = f"https://github.com/{username}/{REPO_NAME}"
            clone    = f"https://github.com/{username}/{REPO_NAME}.git"
        else:
            print(f"❌ {body.get('message', str(e))}"); sys.exit(1)

    print(f"✅  Repo ready: {html_url}")

    # Push
    auth = clone.replace("https://", f"https://{username}:{token}@")
    print("⏳  Pushing code…")
    run("git init")
    # Set git identity if not already configured
    run(f'git config user.name "{username}"')
    run(f'git config user.email "{username}@users.noreply.github.com"')
    run("git add .")
    run('git commit -m "Switch to Ollama" --allow-empty')
    run("git branch -M main")
    run("git remote remove origin 2>/dev/null; true")
    run(f"git remote add origin {auth}")
    run("git push -u origin main --force")

    print(f"\n✅  Done! Code is live at: {html_url}")
    print("\nNext: open Render.com → your service → Redeploy")

if __name__ == "__main__":
    main()
