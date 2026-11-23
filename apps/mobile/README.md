# Magnm Studio Mobile (`apps/mobile`)

Capacitor Android shell and React app for Magnm Studio: Google PKCE sign-in in the system browser, Tailscale laptop pairing, light review, Phone Lite, an offline inbox, share-in and voice dictation.

## Prerequisites

- Node.js 22 and pnpm (`corepack enable` or `pnpm` 12.x)
- JDK 21 (`JAVA_HOME=/usr/lib/jvm/java-21-openjdk`)
- Android SDK with platform `android-36` and `ANDROID_HOME` configured

## Build and test

From `apps/mobile`:

```bash
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
pnpm build
pnpm android:sync
```

Run the Android JVM unit tests, lint checks, and debug APK build with memory-bounded Gradle flags:

```bash
cd android
./gradlew --no-daemon --max-workers=2 testDebugUnitTest lintDebug assembleDebug
```

Or build the web bundle, sync, and assemble the debug APK in one command:

```bash
pnpm android:debug
```

The debug APK is written to:

`apps/mobile/android/app/build/outputs/apk/debug/app-debug.apk`

## Install on a phone

Enable USB debugging on the Android device (or transfer the APK directly) and install the debug APK:

```bash
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

## Security and cleartext trade-off

- Android's `network_security_config.xml` cannot express an IPv4 CIDR such as `100.64.0.0/10` (Tailscale CGNAT) for paired laptops discovered at runtime via QR code.
- Because `HttpURLConnection` obeys Android's network security policy, `res/xml/network_security_config.xml` sets `cleartextTrafficPermitted="true"` on `<base-config>` (with system-only trust anchors) while explicitly setting `cleartextTrafficPermitted="false"` for `supabase.co` and `nvidia.com`.
- To contain that trade-off:
  - WebView keeps `allowMixedContent: false`, `webContentsDebuggingEnabled: false`, `loggingBehavior: 'none'`, and the strict `index.html` Content-Security-Policy (`connect-src 'self'`).
  - Both JS (`src/net.ts`) and native Java (`NativeUrlPolicy.java`) enforce an independent allowlist: `http://` is permitted only to strict dotted-quad IPv4 addresses inside `100.64.0.0/10` with an explicit port and `/phone/v1/` path; `https://` is permitted only to the build-time Supabase origin from `config/supabase.json` (on `/auth/v1/`, `/rest/v1/`, `/functions/v1/ai-proxy`) and `https://integrate.api.nvidia.com/v1/`.

## Phone actions without the laptop

When signed in, Drafts (Add comment, Mark as posted) and Today (Done on a reminder) work with the laptop away: the action is saved on the phone as `Waiting for laptop` (`src/actions.ts`, bound to the account, validated again before sending), sent by itself to the `phone_actions` table (`src/cloudActions.ts`, idempotent by its uuid) and applied by the laptop on its next sync; the phone then shows `Applied` or `Not applied` with the reason, and More lists them all. Text naming a confidential product is refused before it is queued, and confidential items have no actions. No new network destination or Supabase path is used: the table sits under the existing `/rest/v1/` allowlist, so neither allowlist changed. Drafts, comments and open reminders also come from the cloud mirror (`src/mirrorView.ts`), refreshed on open, on foreground and when the connection returns.

## Library, Plan and Create without the laptop (Work tab)

The Work tab has three sections. Everything that needs the laptop is queued through the same `phone_actions` queue (four new kinds: `library_upload`, `schedule_update`, `draft_create`, `workflow_request`); older engines leave kinds they do not know queued.

- **Library** (`src/libraryPhone.ts`, `src/components/LibraryTab.tsx`): upload an image, video or PDF (up to 50 MB; the type is decided by the first bytes, never the name). The file goes in small pieces to the owner's own folder of the private `phone-uploads` bucket (14 days, the shared 1 GB budget) through three native calls (`stageUploadChunk`, `finishCloudUpload`, `abortUpload`; `UploadStage.java` re-checks size and magic bytes before sending, and `NativeUrlPolicy.validateCloudUploadUrl` allows exactly one object path with no query). The laptop then adds it to its Library and removes the cloud copy. Laptop assets come from the cloud mirror (names and sizes, never paths) and are greyed out with "On the laptop" when the laptop is unreachable; when it is reachable they can be fetched by asset id (`/phone/v1/files?asset_id=`, same path as draft files, so no allowlist change).
- **Plan** (`src/planPhone.ts`, `src/components/PlanTab.tsx`): the weekly plan's slots, briefs, the editorial calendar and the schedules come from the cloud mirror. A schedule change or "Plan my week" is queued and shown as waiting until the laptop applies it.
- **Create** (`src/createPhone.ts`, `src/components/CreateTab.tsx`): post, thread and rewrite run on the phone with Phone Lite and are saved as a draft queued for the laptop. Blog, video script, post with image, repurpose, weekly plan and research are queued as workflow requests; the laptop creates the run when it is back and it stops at the normal approval gates. The repository picker only reads the team's non-confidential repositories.

Text naming a confidential product is refused before anything is uploaded or queued, and again by the cloud checks and the laptop.

## Debug builds

The debug APK is debuggable and signed with a throwaway key: anyone with USB debugging access to the phone can read its app data. Use it for testing only, and use a signed release build for daily use. Never disable Android Play Protect or device security checks as the normal path for team builds.

## Release keystore backup and recovery

When creating a release signing keystore for `com.magnm.studio`:

1. **Generate outside the repository**: Store the keystore file outside git (for example `~/magnm-release.jks`). Never commit `*.jks`, `*.keystore`, or `keystore.properties` (all are gitignored).
   ```bash
   keytool -genkeypair -v -keystore ~/magnm-release.jks -alias magnm-studio -keyalg RSA -keysize 2048 -validity 10000
   ```
2. **Two offline copies**: Back up the `.jks`/`.keystore` file and its key alias metadata to two separate offline locations (for example, two encrypted USB drives stored separately).
3. **Password stored separately**: Store the keystore password and key password in a password manager or separate offline note, never alongside the `.jks` backup.
4. **Test a restore**: Copy one backup keystore to a temporary directory and verify it opens with your stored password:
   ```bash
   keytool -list -v -keystore /tmp/test-restore.jks
   ```
   Remove the temporary copy immediately afterward.
5. **What is lost without it**: Android binds an app's package identity (`com.magnm.studio`) to its signing certificate. If the release keystore or its password is lost, you cannot install updates over existing release builds (or publish updates under the same app signing key) — every teammate would have to uninstall the app (wiping local Phone Lite drafts, offline inbox items, and pairing secrets on the phone) and reinstall fresh with a new keystore.

## Owner setup checklist

Before distributing or using this build end-to-end on real devices, the repo owner must configure:

1. **Supabase Auth redirect URL**: Add `com.magnm.studio://auth/callback**` (covering `com.magnm.studio://auth/callback`) to the Redirect URLs allowlist in Supabase Dashboard -> Authentication -> URL Configuration.
2. **Tailscale sign-in**: Install and sign into Tailscale on both the phone and the host laptop running the Magnm engine so the phone can reach the paired `100.64.0.0/10` endpoint.
3. **Release signing key**: Generate, back up (see above), and configure a release keystore outside the repository when ready to publish signed release APKs.
4. **Firebase push notifications (optional)**: Place `google-services.json` in `apps/mobile/android/app/` (gitignored) and wire FCM if push delivery is enabled later.
5. **Physical device testing**: This workspace only runs unit tests, Android lint, and APK assembly; sign-in, QR pairing, share-in, speech recognition, and share-out must be verified on a real Android phone.

## Pitch and Admin without the laptop

Pitch (More, signed in): the laptop mirrors every non-confidential proposal to the cloud (`mirror_records` kind `pitch_proposal`, with the proposal's version, texts, services, prices, link and the data to present it). With internet and no laptop the phone lists them, presents them, makes the quotation PDF, and changes them: a new proposal, client details, texts, which services each sheet shows, prices per proposal, a final quotation, and publishing, copying or withdrawing the client link. Edits are phone actions (`pitch_create`, `pitch_update` with a field whitelist and the version the phone saw, `pitch_finalize`, `pitch_link`) that the laptop applies through the same Pitch functions and rules as the web app (`src/pitchRules.ts`, `src/pitchPhone.ts`, `src/components/PitchEditor.tsx`). If the laptop copy changed first, the phone's edit is kept as a copy named "... (phone)" and nothing is overwritten. Publishing runs on the laptop, so the phone shows "Waiting for the laptop". Quote requests are read straight from the cloud (`src/pitchRequests.ts`).

Admin (only for an active admin; the database enforces it): Team (presence, invites, roles, deactivate), Activity, Workspace, Pitch, Storage, AI keys (labels, health, usage, limits; sharing keys stays on the laptop) and Audit log, in `src/components/AdminTab.tsx` over `src/adminApi.ts`, which uses only the existing tables and functions under `/rest/v1/`. Nothing read there is saved on the phone. Previews are listed (kind, size, expiry); opening the image stays on the desktop app because the phone may only sign the private `phone-files` storage path.
