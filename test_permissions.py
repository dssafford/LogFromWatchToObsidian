#!/usr/bin/env python3
"""Test script to diagnose AppleScript/Reminders permissions."""
import subprocess
import os

print("=== Environment ===")
print(f"USER: {os.environ.get('USER', 'NOT SET')}")
print(f"HOME: {os.environ.get('HOME', 'NOT SET')}")
print(f"DISPLAY: {os.environ.get('DISPLAY', 'NOT SET')}")
print(f"__CFBundleIdentifier: {os.environ.get('__CFBundleIdentifier', 'NOT SET')}")

print("\n=== Test 1: Basic osascript ===")
result = subprocess.run(
    ["osascript", "-e", 'return "hello"'],
    capture_output=True, text=True
)
print(f"Return code: {result.returncode}")
print(f"stdout: {result.stdout.strip()}")
print(f"stderr: {result.stderr.strip()}")

print("\n=== Test 2: List running apps ===")
result = subprocess.run(
    ["osascript", "-e", 'tell application "System Events" to get name of every process whose background only is false'],
    capture_output=True, text=True
)
print(f"Return code: {result.returncode}")
print(f"stdout: {result.stdout[:200] if result.stdout else 'empty'}...")
print(f"stderr: {result.stderr.strip()}")

print("\n=== Test 3: Access Reminders ===")
result = subprocess.run(
    ["osascript", "-e", 'tell application "Reminders" to get name of default list'],
    capture_output=True, text=True
)
print(f"Return code: {result.returncode}")
print(f"stdout: {result.stdout.strip()}")
print(f"stderr: {result.stderr.strip()}")

print("\n=== Test 4: Check launchd context ===")
result = subprocess.run(
    ["launchctl", "managername"],
    capture_output=True, text=True
)
print(f"Launchd manager: {result.stdout.strip() or result.stderr.strip()}")
