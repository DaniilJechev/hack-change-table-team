## ADDED Requirements

### Requirement: DESKTOP-01 Native Windows lifecycle
AvitoCode SHALL start hidden in the Windows tray and show a resizable always-on-top
chat at the right of the cursor display, initially one third of its work area.
Close, minimize and Escape SHALL hide the window; explicit Quit SHALL exit.
Autostart MUST be opt-in; sizes MUST remain visible after display changes.

#### Scenario: Tray lifecycle
- **WHEN** the user starts, opens, resizes and closes the application
- **THEN** one process remains in the tray and the remembered window fits its display

### Requirement: DESKTOP-02 Trusted shared identity
Desktop SHALL authenticate through explicit confirmation by an already bound Telegram
user. Codes MUST expire after five minutes and be single-use; sessions SHALL be
revocable and expire after thirty days. Service secrets MUST remain server-side.
The SSO button SHALL only report that SSO is in development.

#### Scenario: Device confirmation
- **WHEN** the bound user confirms an unexpired device request
- **THEN** only its polling secret can receive a session for that user's stored profile

#### Scenario: Invalid identity
- **WHEN** a code is expired, consumed or a session is revoked
- **THEN** access is denied without disclosing another user's data

### Requirement: DESKTOP-03 Shared chat and capabilities
Desktop and Telegram SHALL use the same agent service, profile and live memory.
New live messages SHALL be persisted and visible in desktop; replies SHALL be
delivered to the originating channel, and background messages to both channels.
H0–H4, role-gated actions and attachments SHALL preserve existing restrictions.
Retries MUST NOT repeat an operation with the same client request identifier.

#### Scenario: Cross-channel continuation
- **WHEN** a user asks in Telegram and continues in desktop
- **THEN** the desktop history includes the first turn and the agent uses the shared context

#### Scenario: Attachment and action ownership
- **WHEN** a user attempts another user's attachment or message action
- **THEN** the request is rejected before invoking an agent tool

#### Scenario: Offline demonstration
- **WHEN** desktop starts a synthetic demo session without corporate accounts
- **THEN** H0–H4 use isolated SeedData and fixtures without reading live data

### Requirement: DESKTOP-04 Resumable notifications
Desktop SHALL maintain event reception while hidden and show one Windows notification
per new incoming reply outside the focused chat. Clicking it SHALL open its message.
History replay MUST NOT generate duplicate notifications. Interrupted operations
SHALL be reported without automatically repeating external actions.

#### Scenario: Hidden response
- **WHEN** an answer arrives while the chat is hidden
- **THEN** a notification opens that answer and the application does not steal focus automatically

#### Scenario: Reconnection
- **WHEN** the event connection is restored or the agent restarts
- **THEN** stored messages are recoverable and completed operations are not repeated

### Requirement: DESKTOP-05 Configurable server
The client SHALL allow configuring an HTTPS API address, including ngrok, and
MUST discard the old session when changing address. HTTP loopback MAY be used
in development only. Tokens SHALL be protected by Windows OS encryption and
MUST NOT be exposed to the renderer. The renderer MUST run isolated without Node.

#### Scenario: Server change
- **WHEN** the user changes the API address
- **THEN** the old connection stops and the new server requires fresh authentication

### Requirement: DESKTOP-06 Telegram copies of desktop replies
When enabled in the user's preferences, live desktop replies, including action and file results, SHALL also be sent to the
user's bound Telegram chat with sources and actions. A client request retry MUST
NOT repeat delivery. Telegram delivery failure MUST NOT fail a saved desktop reply.
Synthetic desktop demo sessions MUST NOT send replies to live Telegram chats.

#### Scenario: Desktop reply mirrored to Telegram
- **WHEN** a live desktop request completes and its client request identifier is retried
- **THEN** its saved answer is delivered to the bound Telegram chat once, including sources and actions

#### Scenario: Telegram unavailable
- **WHEN** Telegram delivery fails after a desktop answer is saved
- **THEN** the desktop request remains completed and its answer is available without repeating the agent operation

#### Scenario: Isolated desktop demo
- **WHEN** a synthetic desktop session receives an answer while a Telegram bot is configured
- **THEN** the answer stays in the synthetic desktop session without Telegram delivery

### Requirement: DESKTOP-07 Personal channel preferences
Authenticated users SHALL independently control Telegram history visibility in
desktop and copying desktop replies to Telegram. Both SHALL default to enabled
and persist in backend per user. Hiding history MUST NOT delete it or alter shared
agent memory or background messages. Hidden Telegram messages MUST NOT notify.

#### Scenario: Saved independent preferences
- **WHEN** a user disables either option and signs in again on a fresh installation
- **THEN** both options retain their individual saved values without affecting another user

#### Scenario: Hide and restore Telegram history
- **WHEN** Telegram history is hidden and later enabled again
- **THEN** its stored messages reappear, while desktop and background messages remain visible throughout

#### Scenario: Disable outgoing copies
- **WHEN** copying desktop replies is disabled and a desktop request completes
- **THEN** the answer is saved in desktop without sending it to Telegram
