# appstore-ready

Scan your Expo or React Native project for App Store rejection causes before you submit.

Zero dependencies. Works offline. Exits with code 1 on critical issues so it works in CI.

```bash
python3 appstore_ready.py /path/to/your/app
```

---

## Why

App Store review takes 1-3 days. Getting rejected over a missing `NSCameraUsageDescription` or an uppercase Android package name wastes a week. This tool catches those issues in seconds before you submit.

## Install

No install needed. Copy one file and run it.

```bash
curl -O https://raw.githubusercontent.com/ashwin400/appstore-ready/main/appstore_ready.py
python3 appstore_ready.py .
```

## Usage

```bash
# Scan current directory
python3 appstore_ready.py .

# Scan specific project
python3 appstore_ready.py /path/to/project

# iOS only
python3 appstore_ready.py . --platform ios

# Show fix instructions
python3 appstore_ready.py . --fix-hints

# Only show critical/high issues
python3 appstore_ready.py . --severity critical

# JSON output for CI
python3 appstore_ready.py . --json
```

## What It Checks

### iOS (will get you rejected)
- Missing `NSCameraUsageDescription` when camera APIs are in use
- Missing `NSPhotoLibraryUsageDescription` when photo picker is used
- Missing `NSMicrophoneUsageDescription` when audio recording is used
- Missing `NSLocationWhenInUseUsageDescription` for location features
- Missing Privacy Manifest (`PrivacyInfo.xcprivacy`) for required reason APIs
- Hardcoded HTTP URLs in production code (App Transport Security blocks them)
- Dev/staging API endpoints left in production builds

### Android
- Uppercase characters in package name (`com.MyApp.app` fails Play Store)
- Missing required permissions for used APIs
- Debug flags enabled in release builds

### Both platforms
- Hardcoded API keys and secrets in source code
- Missing version bump before submission
- EAS build configuration issues

## CI Integration

```yaml
# .github/workflows/submit.yml
- name: App Store readiness check
  run: python3 appstore_ready.py . --severity critical
  # Exits 1 on any critical finding, blocking the workflow
```

## Example Output

```
appstore-ready scan: my-app/
Checks: 24   Found: 3 issues

● CRITICAL  [iOS] NSCameraUsageDescription missing
  Camera APIs detected but usage description not set in app.json.
  Fix: Add "ios.infoPlist.NSCameraUsageDescription" to app.json

● CRITICAL  [Android] Invalid package name
  Package "com.MyApp.food" contains uppercase letters.
  Fix: Change to "com.myapp.food" in app.json

◐ MEDIUM    Hardcoded staging URL
  "https://staging-api.myapp.com" found in src/config.ts:14
  Fix: Use environment variables for API endpoints

Exit code: 1 (critical issues found)
```

## Requirements

Python 3.9+. No dependencies. Works with Expo SDK 49+.
