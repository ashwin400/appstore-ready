#!/usr/bin/env python3
"""
appstore-ready — App Store Readiness Checker for Expo/React Native

Scans a project and flags common App Store rejection causes before you submit.
Zero dependencies. Works offline. Expo + bare React Native.

Usage:
  python3 appstore_ready.py /path/to/expo-project
  python3 appstore_ready.py .
  python3 appstore_ready.py . --json
  python3 appstore_ready.py . --fix-hints   # Show fix instructions
  python3 appstore_ready.py . --platform ios  # iOS only
  python3 appstore_ready.py . --platform android

Author: Atom (Build #44, 2026-02-17)
"""

import utils.transport
import utils.session
import json
import os
import re
import sys
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ─────────────────────────── ANSI Colors ────────────────────────────────────

def supports_color():
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

USE_COLOR = supports_color()

class C:
    RESET  = "\033[0m"   if USE_COLOR else ""
    RED    = "\033[91m"  if USE_COLOR else ""
    YELLOW = "\033[93m"  if USE_COLOR else ""
    GREEN  = "\033[92m"  if USE_COLOR else ""
    BLUE   = "\033[94m"  if USE_COLOR else ""
    CYAN   = "\033[96m"  if USE_COLOR else ""
    BOLD   = "\033[1m"   if USE_COLOR else ""
    DIM    = "\033[2m"   if USE_COLOR else ""

# ─────────────────────────── Data Types ─────────────────────────────────────

class Severity(Enum):
    CRITICAL = "critical"  # Will be rejected
    HIGH     = "high"      # Very likely to be rejected
    MEDIUM   = "medium"    # Should fix before submission
    LOW      = "low"       # Nice to have

class Platform(Enum):
    IOS     = "ios"
    ANDROID = "android"
    BOTH    = "both"

@dataclass
class Finding:
    severity: Severity
    code: str
    title: str
    description: str
    platform: Platform = Platform.BOTH
    fix: str = ""
    file: str = ""
    line: Optional[int] = None

    def severity_label(self):
        labels = {
            Severity.CRITICAL: f"{C.RED}{C.BOLD}● CRITICAL{C.RESET}",
            Severity.HIGH:     f"{C.RED}● HIGH{C.RESET}",
            Severity.MEDIUM:   f"{C.YELLOW}◐ MEDIUM{C.RESET}",
            Severity.LOW:      f"{C.BLUE}○ LOW{C.RESET}",
        }
        return labels[self.severity]

    def platform_label(self):
        icons = {Platform.IOS: "🍎", Platform.ANDROID: "🤖", Platform.BOTH: "📱"}
        return icons[self.platform]

# ─────────────────────────── Checker ────────────────────────────────────────

class AppStoreChecker:

    # iOS permission usage description keys → human name, common triggers
    IOS_USAGE_KEYS = {
        "NSCameraUsageDescription": {
            "name": "Camera",
            "triggers": ["Camera", "camera", "expo-camera", "react-native-camera", "RNCamera",
                        "ImagePicker", "imagePicker", "receipt", "scan", "OCR"],
            "fix": "Add NSCameraUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSMicrophoneUsageDescription": {
            "name": "Microphone",
            "triggers": ["microphone", "Microphone", "requestRecordingPermissionsAsync",
                        "Recording", "startRecording", "AudioRecorder", "AVAudioRecorder",
                        "RECORD_AUDIO", "react-native-audio-recorder", "expo-speech",
                        "MediaRecorder", "getUserMedia"],
            "fix": "Add NSMicrophoneUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSPhotoLibraryUsageDescription": {
            "name": "Photo Library (read)",
            "triggers": ["MediaLibrary", "mediaLibrary", "Photos", "gallery", "album",
                        "expo-media-library", "ImagePicker", "photo"],
            "fix": "Add NSPhotoLibraryUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSPhotoLibraryAddUsageDescription": {
            "name": "Photo Library (write)",
            "triggers": ["saveToLibrary", "savePhoto", "CameraRoll", "MediaLibrary.saveToLibraryAsync"],
            "fix": "Add NSPhotoLibraryAddUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSLocationWhenInUseUsageDescription": {
            "name": "Location (when in use)",
            "triggers": ["Location", "location", "expo-location", "getCurrentPosition",
                        "watchPosition", "geolocation", "coordinates"],
            "fix": "Add NSLocationWhenInUseUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSLocationAlwaysUsageDescription": {
            "name": "Location (always)",
            "triggers": ["BackgroundFetch", "startLocationUpdatesAsync", "watchPositionAsync"],
            "fix": "Add NSLocationAlwaysUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSFaceIDUsageDescription": {
            "name": "Face ID",
            "triggers": ["LocalAuthentication", "expo-local-authentication", "BiometricAuth",
                        "faceId", "FaceID", "biometric"],
            "fix": "Add NSFaceIDUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSContactsUsageDescription": {
            "name": "Contacts",
            "triggers": ["Contacts", "expo-contacts", "react-native-contacts", "addressBook"],
            "fix": "Add NSContactsUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSCalendarsUsageDescription": {
            "name": "Calendar",
            "triggers": ["Calendar", "expo-calendar", "react-native-calendar"],
            "fix": "Add NSCalendarsUsageDescription to expo.ios.infoPlist in app.json",
        },
        "NSUserTrackingUsageDescription": {
            "name": "Tracking (ATT)",
            "triggers": ["AppTrackingTransparency", "requestTrackingAuthorization",
                        "expo-tracking-transparency", "analytics", "amplitude", "mixpanel",
                        "facebook-ads", "google-analytics"],
            "fix": "Add NSUserTrackingUsageDescription and implement ATT request flow",
        },
        "NSBluetoothAlwaysUsageDescription": {
            "name": "Bluetooth",
            "triggers": ["Bluetooth", "BluetoothLE", "expo-bluetooth", "react-native-ble",
                        "react-native-ble-plx", "BLEManager", "bluetooth.connect"],
            "fix": "Add NSBluetoothAlwaysUsageDescription to expo.ios.infoPlist",
        },
        "NSHealthShareUsageDescription": {
            "name": "HealthKit (read)",
            "triggers": ["HealthKit", "expo-health", "react-native-health",
                        "HKQuantityType", "stepCount", "heartRate", "HKSampleQuery"],
            "fix": "Add NSHealthShareUsageDescription to expo.ios.infoPlist",
        },
        "NFCReaderUsageDescription": {
            "name": "NFC",
            "triggers": ["NFC", "nfc", "expo-nfc"],
            "fix": "Add NFCReaderUsageDescription to expo.ios.infoPlist",
        },
    }

    # Patterns that indicate HTTP (non-HTTPS) requests in code
    HTTP_PATTERNS = [
        r'["\']http://(?!localhost|127\.0\.0\.1|10\.\d+|192\.168)',
        r'fetch\(["\']http://',
        r'axios\.(?:get|post|put|delete|patch)\(["\']http://',
    ]

    # Patterns that look like hardcoded credentials
    SECRET_PATTERNS = [
        (r'(?i)(?:api_key|apikey|api-key)\s*[=:]\s*["\']([A-Za-z0-9_\-]{16,})["\']', "API Key"),
        (r'(?i)(?:secret|password|passwd|pwd)\s*[=:]\s*["\']([^"\']{8,})["\']', "Secret/Password"),
        (r'sk-[A-Za-z0-9]{20,}', "OpenAI API Key"),
        (r'AIza[A-Za-z0-9_\-]{30,}', "Google API Key"),
        (r'pk_live_[A-Za-z0-9]{20,}', "Stripe Live Key"),
        (r'sk_live_[A-Za-z0-9]{20,}', "Stripe Live Secret"),
        (r'AAAA[A-Za-z0-9_\-]{140,}', "Firebase Server Key"),
    ]

    # Patterns that indicate debug/dev URLs in production
    DEV_URL_PATTERNS = [
        r'(?:https?://)?localhost[:/]',
        r'(?:https?://)?127\.0\.0\.1[:/]',
        r'ngrok\.io',
        r'\.local[:/]',
        r':4000["/]',
        r':3000["/]',
        r':8080["/]',
        r':8000["/]',
    ]

    def __init__(self, project_path: str, platform: str = "both"):
        self.project_path = Path(project_path).resolve()
        self.platform_filter = platform
        self.findings: list[Finding] = []
        self.app_config = {}
        self.package_json = {}
        self.source_files_cache = {}

    def run(self) -> list[Finding]:
        self._load_configs()
        self._check_app_config()
        self._check_usage_descriptions()
        self._check_assets()
        self._check_privacy_manifest()
        self._check_source_secrets()
        self._check_source_http()
        self._check_source_dev_urls()
        self._check_eas_config()
        self._check_android_config()
        return self.findings

    # ─────────────────── Config Loaders ──────────────────────────────────────

    def _load_configs(self):
        """Load app.json, app.config.js (static only), package.json."""
        app_json_path = self.project_path / "app.json"
        if app_json_path.exists():
            try:
                with open(app_json_path) as f:
                    data = json.load(f)
                    self.app_config = data.get("expo", data)
            except Exception:
                pass

        pkg_path = self.project_path / "package.json"
        if pkg_path.exists():
            try:
                with open(pkg_path) as f:
                    self.package_json = json.load(f)
            except Exception:
                pass

    def _load_source_files(self) -> dict[str, str]:
        """Scan JS/TS/TSX source files. Cache results."""
        if self.source_files_cache:
            return self.source_files_cache

        extensions = {'.js', '.ts', '.jsx', '.tsx', '.mjs'}
        skip_dirs = {'node_modules', '.expo', 'android', 'ios', 'dist', '.git', '__tests__', '.cache'}

        result = {}
        try:
            for root, dirs, files in os.walk(self.project_path):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                for fname in files:
                    if Path(fname).suffix in extensions:
                        fpath = Path(root) / fname
                        try:
                            content = fpath.read_text(encoding='utf-8', errors='ignore')
                            rel = str(fpath.relative_to(self.project_path))
                            result[rel] = content
                        except Exception:
                            pass
        except Exception:
            pass

        self.source_files_cache = result
        return result

    def _add(self, finding: Finding):
        # Apply platform filter
        if self.platform_filter == "ios" and finding.platform == Platform.ANDROID:
            return
        if self.platform_filter == "android" and finding.platform == Platform.IOS:
            return
        self.findings.append(finding)

    # ─────────────────── Check: App Config Basics ────────────────────────────

    def _check_app_config(self):
        if not self.app_config:
            self._add(Finding(
                severity=Severity.CRITICAL,
                code="NO_APP_CONFIG",
                title="No app.json or expo config found",
                description="Expo requires an app.json (or app.config.js) to build for App Store submission.",
                fix="Create app.json with required Expo configuration.",
            ))
            return

        # Version check
        version = self.app_config.get("version", "")
        if not version:
            self._add(Finding(
                severity=Severity.HIGH,
                code="MISSING_VERSION",
                title="Missing app version",
                description="The 'version' field is required for App Store submission.",
                fix="Set 'version' in app.json (e.g. '1.0.0')",
            ))
        elif not re.match(r'^\d+\.\d+\.\d+$', str(version)):
            self._add(Finding(
                severity=Severity.MEDIUM,
                code="BAD_VERSION_FORMAT",
                title=f"Version '{version}' is not semantic (X.Y.Z)",
                description="App Store expects a version like 1.0.0. Non-standard formats may cause issues.",
                fix=f"Change version to '1.0.0' format in app.json",
            ))

        # iOS config
        ios_config = self.app_config.get("ios", {})

        # Bundle identifier
        bundle_id = ios_config.get("bundleIdentifier", "")
        if not bundle_id:
            self._add(Finding(
                severity=Severity.CRITICAL,
                code="MISSING_BUNDLE_ID",
                title="Missing iOS bundleIdentifier",
                description="A bundle identifier (e.g. com.yourcompany.yourapp) is required for App Store submission.",
                platform=Platform.IOS,
                fix="Add 'bundleIdentifier' to expo.ios in app.json",
            ))
        elif not re.match(r'^[a-zA-Z][a-zA-Z0-9]*(\.[a-zA-Z][a-zA-Z0-9]*)+$', bundle_id):
            self._add(Finding(
                severity=Severity.HIGH,
                code="INVALID_BUNDLE_ID",
                title=f"Bundle identifier '{bundle_id}' has invalid format",
                description="Bundle IDs must be reverse domain notation with alphanumeric segments (e.g. com.company.app).",
                platform=Platform.IOS,
                fix="Use format: com.yourcompany.yourapp (only letters, digits, dots)",
            ))

        # Build number
        build_number = ios_config.get("buildNumber", "")
        if not build_number:
            self._add(Finding(
                severity=Severity.HIGH,
                code="MISSING_BUILD_NUMBER",
                title="Missing iOS buildNumber",
                description="A build number is required. Must be greater than the previously uploaded build.",
                platform=Platform.IOS,
                fix="Add 'buildNumber' to expo.ios in app.json (e.g. '1')",
            ))

        # Apple Team ID
        team_id = ios_config.get("appleTeamId", "")
        if not team_id:
            self._add(Finding(
                severity=Severity.HIGH,
                code="MISSING_TEAM_ID",
                title="Missing Apple Team ID",
                description="The Apple Team ID is needed for EAS Build and App Store submission.",
                platform=Platform.IOS,
                fix="Add 'appleTeamId' to expo.ios in app.json (10-char alphanumeric ID from developer.apple.com)",
            ))

        # App name
        if not self.app_config.get("name", ""):
            self._add(Finding(
                severity=Severity.CRITICAL,
                code="MISSING_APP_NAME",
                title="Missing app name",
                description="The app name is displayed in the App Store and on device home screens.",
                fix="Set 'name' in app.json",
            ))

        # Encryption declaration (common oversight)
        info_plist = ios_config.get("infoPlist", {})
        if "ITSAppUsesNonExemptEncryption" not in info_plist:
            self._add(Finding(
                severity=Severity.HIGH,
                code="MISSING_ENCRYPTION_DECLARATION",
                title="Missing ITSAppUsesNonExemptEncryption declaration",
                description=(
                    "Apple requires declaring whether your app uses non-exempt encryption. "
                    "Missing this causes App Store submission failure. "
                    "Most apps using HTTPS/TLS (standard) can declare false."
                ),
                platform=Platform.IOS,
                fix='Add to expo.ios.infoPlist in app.json: "ITSAppUsesNonExemptEncryption": false',
            ))

    # ─────────────────── Check: Usage Descriptions ───────────────────────────

    def _check_usage_descriptions(self):
        """Check for required iOS permission usage descriptions."""
        ios_config = self.app_config.get("ios", {})
        info_plist = ios_config.get("infoPlist", {})

        # Gather plugins list for trigger scanning
        plugins = self.app_config.get("plugins", [])
        plugin_names = set()
        for p in plugins:
            if isinstance(p, str):
                plugin_names.add(p)
            elif isinstance(p, list) and p:
                plugin_names.add(p[0])

        # Gather package dependencies for trigger scanning
        all_deps = {
            **self.package_json.get("dependencies", {}),
            **self.package_json.get("devDependencies", {}),
        }

        # Load source files for trigger scanning
        source_files = self._load_source_files()
        source_combined = "\n".join(source_files.values())[:200_000]  # cap for performance

        for plist_key, meta in self.IOS_USAGE_KEYS.items():
            if plist_key in info_plist:
                # Present — check it has actual text (not empty)
                value = info_plist[plist_key]
                if not value or str(value).strip() == "":
                    self._add(Finding(
                        severity=Severity.HIGH,
                        code=f"EMPTY_USAGE_{plist_key.upper()}",
                        title=f"{meta['name']} usage description is empty",
                        description=(
                            f"{plist_key} is present but has no text. "
                            "Apple reviews the description and rejects vague or empty ones."
                        ),
                        platform=Platform.IOS,
                        fix=f"Set a clear user-facing reason: why does the app need {meta['name'].lower()} access?",
                    ))
                continue  # already declared

            # Check if permission is actually needed (scan triggers)
            needed = False
            trigger_found = ""
            for trigger in meta["triggers"]:
                if trigger in source_combined:
                    needed = True
                    trigger_found = f"found '{trigger}' in source code"
                    break
                if trigger in plugin_names:
                    needed = True
                    trigger_found = f"plugin '{trigger}' in app.json"
                    break
                if trigger in all_deps:
                    needed = True
                    trigger_found = f"package '{trigger}' in package.json"
                    break

            if needed:
                self._add(Finding(
                    severity=Severity.CRITICAL,
                    code=f"MISSING_{plist_key.upper()}",
                    title=f"Missing {plist_key}",
                    description=(
                        f"Your app uses {meta['name']} ({trigger_found}) but has no usage description. "
                        "App Store will reject the build. The description is shown to users in the permission dialog."
                    ),
                    platform=Platform.IOS,
                    fix=meta["fix"],
                ))

    # ─────────────────── Check: Assets ───────────────────────────────────────

    def _check_assets(self):
        icon = self.app_config.get("icon", "")
        if not icon:
            self._add(Finding(
                severity=Severity.CRITICAL,
                code="MISSING_ICON",
                title="No app icon configured",
                description="A 1024×1024 PNG icon is required for App Store submission.",
                fix="Set 'icon' in app.json pointing to a 1024×1024 PNG file",
            ))
        else:
            icon_path = self.project_path / icon.lstrip("./")
            if not icon_path.exists():
                self._add(Finding(
                    severity=Severity.CRITICAL,
                    code="ICON_FILE_MISSING",
                    title=f"Icon file not found: {icon}",
                    description=f"The configured icon path '{icon}' does not exist on disk.",
                    fix=f"Ensure {icon} exists in the project",
                    file=icon,
                ))

        # Adaptive icon (Android)
        android_config = self.app_config.get("android", {})
        adaptive = android_config.get("adaptiveIcon", {})
        if not adaptive.get("foregroundImage") and not android_config.get("icon"):
            self._add(Finding(
                severity=Severity.HIGH,
                code="MISSING_ANDROID_ICON",
                title="No Android icon configured",
                description="Android requires an adaptive icon or a regular icon.",
                platform=Platform.ANDROID,
                fix="Add 'android.adaptiveIcon' or 'android.icon' to app.json",
            ))

        # Splash screen
        plugins = self.app_config.get("plugins", [])
        has_splash = any(
            (isinstance(p, str) and "splash" in p.lower()) or
            (isinstance(p, list) and p and "splash" in str(p[0]).lower())
            for p in plugins
        )
        if not has_splash and not self.app_config.get("splash"):
            self._add(Finding(
                severity=Severity.MEDIUM,
                code="MISSING_SPLASH",
                title="No splash screen configured",
                description="A splash screen improves UX and is required for a polished App Store listing.",
                fix="Add expo-splash-screen plugin to app.json plugins array",
            ))

    # ─────────────────── Check: Privacy Manifest ─────────────────────────────

    def _check_privacy_manifest(self):
        """Check for Apple's required PrivacyInfo.xcprivacy (required since Spring 2024)."""
        # Look for privacy manifest in ios/ or root
        ios_dir = self.project_path / "ios"
        manifest_locations = [
            ios_dir / "PrivacyInfo.xcprivacy",
            ios_dir / self.app_config.get("name", "") / "PrivacyInfo.xcprivacy",
            self.project_path / "PrivacyInfo.xcprivacy",
        ]
        found = any(p.exists() for p in manifest_locations)

        # Also check if any Expo plugin generates one
        plugins = self.app_config.get("plugins", [])
        plugin_str = str(plugins).lower()
        if "privacy" in plugin_str or "privacymanifests" in plugin_str:
            found = True

        if not found and ios_dir.exists():
            self._add(Finding(
                severity=Severity.HIGH,
                code="MISSING_PRIVACY_MANIFEST",
                title="Missing PrivacyInfo.xcprivacy (Apple Privacy Manifest)",
                description=(
                    "Since Spring 2024, Apple requires a privacy manifest for all apps. "
                    "Without it, App Store Connect will warn or reject your submission. "
                    "Declare which APIs your app (and its dependencies) use that access user data."
                ),
                platform=Platform.IOS,
                fix=(
                    "Add ios/PrivacyInfo.xcprivacy to your Expo project or use an Expo plugin that "
                    "generates it. See: https://docs.expo.dev/guides/privacy-manifests/"
                ),
            ))

    # ─────────────────── Check: Source Code Issues ───────────────────────────

    def _check_source_secrets(self):
        """Check for hardcoded secrets in source code."""
        source_files = self._load_source_files()
        skip_patterns = {'node_modules', '.env', 'test', '__tests__', 'spec', '.example', 'mock'}

        for rel_path, content in source_files.items():
            # Skip test files and mocks
            if any(s in rel_path.lower() for s in skip_patterns):
                continue

            for pattern, secret_type in self.SECRET_PATTERNS:
                matches = list(re.finditer(pattern, content))
                for match in matches[:1]:  # Max 1 finding per file per pattern
                    # Find approximate line number
                    line_num = content[:match.start()].count('\n') + 1
                    self._add(Finding(
                        severity=Severity.HIGH,
                        code="HARDCODED_SECRET",
                        title=f"Possible hardcoded {secret_type} in source",
                        description=(
                            f"Found what looks like a hardcoded {secret_type} in {rel_path}. "
                            "Hardcoded credentials in app bundles can be extracted and abused. "
                            "Apple may reject apps with exposed API keys."
                        ),
                        fix="Move secrets to environment variables or a secrets manager. Use EAS Secrets for build-time env vars.",
                        file=rel_path,
                        line=line_num,
                    ))

    def _check_source_http(self):
        """Check for HTTP (non-HTTPS) network requests."""
        source_files = self._load_source_files()

        for rel_path, content in source_files.items():
            for pattern in self.HTTP_PATTERNS:
                if re.search(pattern, content):
                    self._add(Finding(
                        severity=Severity.HIGH,
                        code="HTTP_REQUEST",
                        title=f"HTTP (non-HTTPS) request detected in {rel_path}",
                        description=(
                            "Apple's App Transport Security requires HTTPS for all network connections. "
                            "Apps with HTTP requests may be rejected unless an ATS exception is declared."
                        ),
                        fix="Replace HTTP URLs with HTTPS. If unavoidable, add an NSAppTransportSecurity exception in infoPlist.",
                        file=rel_path,
                    ))
                    break  # One finding per file

    def _check_source_dev_urls(self):
        """Check for development/localhost URLs hardcoded in source."""
        source_files = self._load_source_files()

        for rel_path, content in source_files.items():
            # Skip config and env files
            if any(s in rel_path.lower() for s in ['.env', 'config', 'test', 'spec', '__tests__']):
                continue

            for pattern in self.DEV_URL_PATTERNS:
                matches = list(re.finditer(pattern, content))
                if matches:
                    line_num = content[:matches[0].start()].count('\n') + 1
                    self._add(Finding(
                        severity=Severity.MEDIUM,
                        code="DEV_URL_IN_SOURCE",
                        title=f"Development URL in source: {rel_path}:{line_num}",
                        description=(
                            "A localhost or development URL was found in source code. "
                            "This could mean users are connecting to a dev server instead of production."
                        ),
                        fix="Use environment variables for API URLs so dev/prod can be switched at build time.",
                        file=rel_path,
                        line=line_num,
                    ))
                    break

    # ─────────────────── Check: EAS Config ───────────────────────────────────

    def _check_eas_config(self):
        """Check eas.json for production build configuration."""
        eas_path = self.project_path / "eas.json"
        if not eas_path.exists():
            self._add(Finding(
                severity=Severity.MEDIUM,
                code="MISSING_EAS_JSON",
                title="No eas.json found",
                description="EAS Build requires eas.json to configure build profiles.",
                fix="Run 'eas build:configure' to generate eas.json",
            ))
            return

        try:
            eas_config = json.loads(eas_path.read_text())
            build = eas_config.get("build", {})

            if "production" not in build:
                self._add(Finding(
                    severity=Severity.HIGH,
                    code="NO_PRODUCTION_BUILD_PROFILE",
                    title="No 'production' build profile in eas.json",
                    description="A production profile is needed for App Store builds.",
                    fix='Add a "production" profile to the build section of eas.json',
                ))

            # Check for store distribution in production profile
            prod = build.get("production", {})
            ios_prod = prod.get("ios", {})
            if ios_prod.get("distribution") not in ["store", None]:
                if ios_prod.get("distribution") == "simulator":
                    self._add(Finding(
                        severity=Severity.CRITICAL,
                        code="SIMULATOR_DISTRIBUTION",
                        title="Production profile targets simulator builds",
                        description="Simulator builds cannot be submitted to the App Store. Use distribution: 'store'.",
                        platform=Platform.IOS,
                        fix='Set distribution: "store" in eas.json build.production.ios',
                    ))
        except Exception:
            pass

    # ─────────────────── Check: Android Config ───────────────────────────────

    def _check_android_config(self):
        android_config = self.app_config.get("android", {})

        package_name = android_config.get("package", "")
        if not package_name:
            self._add(Finding(
                severity=Severity.CRITICAL,
                code="MISSING_ANDROID_PACKAGE",
                title="Missing Android package name",
                description="An Android package name (e.g. com.yourcompany.yourapp) is required for Play Store submission.",
                platform=Platform.ANDROID,
                fix="Add 'package' to expo.android in app.json",
            ))
        elif not re.match(r'^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$', package_name):
            self._add(Finding(
                severity=Severity.MEDIUM,
                code="ANDROID_PACKAGE_UPPERCASE",
                title=f"Android package '{package_name}' contains uppercase letters",
                description="Android package names should be all lowercase.",
                platform=Platform.ANDROID,
                fix=f"Change package to lowercase: {package_name.lower()}",
            ))

        version_code = android_config.get("versionCode", None)
        if version_code is None:
            self._add(Finding(
                severity=Severity.HIGH,
                code="MISSING_VERSION_CODE",
                title="Missing Android versionCode",
                description="versionCode is required and must be incremented with each Play Store release.",
                platform=Platform.ANDROID,
                fix="Add 'versionCode' (integer) to expo.android in app.json",
            ))

# ─────────────────── Output ──────────────────────────────────────────────────

def severity_sort_key(f: Finding) -> int:
    order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    return order[f.severity]

def print_report(findings: list[Finding], show_fixes: bool = False, project_path: str = ""):
    if not findings:
        print(f"\n{C.GREEN}{C.BOLD}✅ No issues found! App looks ready for submission.{C.RESET}\n")
        return

    findings_sorted = sorted(findings, key=severity_sort_key)

    critical = [f for f in findings if f.severity == Severity.CRITICAL]
    high     = [f for f in findings if f.severity == Severity.HIGH]
    medium   = [f for f in findings if f.severity == Severity.MEDIUM]
    low      = [f for f in findings if f.severity == Severity.LOW]

    print(f"\n{C.BOLD}📱 App Store Readiness Report{C.RESET}", end="")
    if project_path:
        print(f"  {C.DIM}{project_path}{C.RESET}", end="")
    print(f"\n{'─'*60}")

    # Summary bar
    parts = []
    if critical: parts.append(f"{C.RED}{C.BOLD}{len(critical)} critical{C.RESET}")
    if high:     parts.append(f"{C.RED}{len(high)} high{C.RESET}")
    if medium:   parts.append(f"{C.YELLOW}{len(medium)} medium{C.RESET}")
    if low:      parts.append(f"{C.BLUE}{len(low)} low{C.RESET}")
    print(f"Found {len(findings)} issues: {' │ '.join(parts)}\n")

    for f in findings_sorted:
        loc = f" {C.DIM}({f.file}:{f.line}){C.RESET}" if f.file and f.line else \
              f" {C.DIM}({f.file}){C.RESET}" if f.file else ""
        print(f"  {f.severity_label()} {f.platform_label()}  {C.BOLD}{f.title}{C.RESET}{loc}")
        print(f"      {C.DIM}{f.description[:120]}...{C.RESET}" if len(f.description) > 120 else f"      {C.DIM}{f.description}{C.RESET}")
        if show_fixes and f.fix:
            print(f"      {C.CYAN}FIX: {f.fix}{C.RESET}")
        print()

    # Bottom line
    print(f"{'─'*60}")
    if critical:
        print(f"{C.RED}{C.BOLD}🚫 NOT READY — Fix {len(critical)} critical issue(s) before submitting.{C.RESET}")
    elif high:
        print(f"{C.RED}⚠️  Likely to be rejected — address {len(high)} high-severity issue(s).{C.RESET}")
    elif medium:
        print(f"{C.YELLOW}◐ Mostly ready — {len(medium)} medium issue(s) to polish.{C.RESET}")
    else:
        print(f"{C.GREEN}✅ Looks good — only low-severity suggestions remain.{C.RESET}")
    print()

def print_json(findings: list[Finding]):
    data = []
    for f in sorted(findings, key=severity_sort_key):
        data.append({
            "severity": f.severity.value,
            "code": f.code,
            "title": f.title,
            "description": f.description,
            "platform": f.platform.value,
            "fix": f.fix,
            "file": f.file,
            "line": f.line,
        })
    print(json.dumps({"findings": data, "total": len(data)}, indent=2))

# ─────────────────── CLI ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="appstore-ready — Scan Expo/React Native projects for App Store rejection risks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 appstore_ready.py .
  python3 appstore_ready.py /path/to/project --fix-hints
  python3 appstore_ready.py . --platform ios
  python3 appstore_ready.py . --json
  python3 appstore_ready.py . --severity critical,high
        """,
    )
    parser.add_argument("path", nargs="?", default=".", help="Project root directory (default: .)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable report")
    parser.add_argument("--fix-hints", action="store_true", help="Show fix instructions for each finding")
    parser.add_argument("--platform", choices=["ios", "android", "both"], default="both",
                        help="Only check iOS or Android specific issues")
    parser.add_argument("--severity", default="all",
                        help="Filter by severity: critical, high, medium, low (comma-separated, default: all)")

    args = parser.parse_args()

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Error: path '{args.path}' does not exist", file=sys.stderr)
        sys.exit(1)

    checker = AppStoreChecker(str(project_path), platform=args.platform)
    findings = checker.run()

    # Apply severity filter
    if args.severity != "all":
        allowed = {s.strip().lower() for s in args.severity.split(",")}
        findings = [f for f in findings if f.severity.value in allowed]

    if args.json:
        print_json(findings)
    else:
        print_report(findings, show_fixes=args.fix_hints, project_path=str(project_path))

    # Exit code: 1 if any critical/high
    has_blocking = any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings)
    sys.exit(1 if has_blocking else 0)

if __name__ == "__main__":
    main()
