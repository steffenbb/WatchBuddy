# Setup Flow Enhancement: TMDB Integration

## Overview
Enhanced the WatchBuddy setup flow to require **both** Trakt and TMDB API configuration before allowing users to proceed to the dashboard. TMDB is essential for the app to function properly (metadata, posters, descriptions, ratings, etc.).

## Changes Made

### Backend: `/api/settings` Endpoints

#### New Endpoint: `/api/settings/validate-setup`
**Purpose:** Comprehensive validation of all required setup steps

**Returns:**
```json
{
  "valid": true/false,
  "trakt_configured": boolean,
  "trakt_authenticated": boolean,
  "tmdb_configured": boolean,
  "errors": ["error message 1", "error message 2"],
  "warnings": ["warning message"],
  "message": "Setup complete" | "Setup incomplete"
}
```

**Validation Checks:**
1. Trakt credentials exist (client_id, client_secret)
2. Trakt OAuth completed (access token exists)
3. TMDB API key configured
4. TMDB API key is valid (makes test request to TMDB API)

#### Existing TMDB Endpoints (already in place):
- `POST /api/settings/tmdb-key` - Save and validate TMDB key
- `GET /api/settings/tmdb-key` - Get TMDB key status
- `GET /api/settings/tmdb-key/status` - Check if key is valid

### Frontend: SetupScreen Component

#### Complete Redesign with 3-Step Flow

**Step 1: Trakt Credentials**
- User provides Trakt Client ID and Client Secret
- Clear instructions with copy-to-clipboard for redirect URI
- Validates both fields are filled before proceeding
- Saves to backend: `POST /api/settings/trakt-credentials`

**Step 2: TMDB API Key** ⭐ NEW
- User provides TMDB API key
- Warning banner explains TMDB is required for app functionality
- Key is validated against TMDB API before saving
- Instructions link to TMDB settings page
- Saves to backend: `POST /api/settings/tmdb-key`
- Backend validates key works before accepting

**Step 3: OAuth Connection**
- Shows confirmation that both APIs are configured
- User clicks "Connect to Trakt" to authorize
- Redirects to Trakt OAuth flow
- Returns to app after authorization

**Progress Indicator:**
- Visual stepper showing all 3 steps
- Green checkmarks for completed steps
- Current step highlighted in fuchsia
- Allows going back to previous steps

**Final Validation:**
- After OAuth completes, calls `/api/settings/validate-setup`
- Only redirects to dashboard if ALL checks pass:
  - ✅ Trakt credentials configured
  - ✅ Trakt authenticated
  - ✅ TMDB key configured and valid
- Shows error toast if any check fails
- Returns to appropriate step if validation fails

### State Management

**Setup Check on Load:**
```typescript
checkSetupStatus() {
  - Check /api/settings/trakt-credentials
  - Check /api/settings/tmdb-key
  - Check /api/trakt/status
  - Determine which step to show
}
```

**Validation Before Dashboard:**
```typescript
validateAndRedirect() {
  - Call /api/settings/validate-setup
  - If valid: redirect to dashboard
  - If invalid: show errors, return to failed step
}
```

## User Experience Flow

### New User (First Time Setup)
1. Lands on setup screen
2. **Step 1:** Enters Trakt credentials → Saves → Auto-advances
3. **Step 2:** Enters TMDB key → Validates → Auto-advances
4. **Step 3:** Clicks "Connect to Trakt" → OAuth → Returns
5. System validates both APIs → Redirects to dashboard

### Returning User (OAuth Expired)
1. Lands on setup screen
2. Steps 1 & 2 show green checkmarks (already configured)
3. Auto-advances to Step 3
4. Clicks "Connect to Trakt" → OAuth → Redirects to dashboard

### Partial Setup
- If only Trakt configured: starts at TMDB step
- If only TMDB configured: starts at Trakt step
- Can navigate back to fix credentials if needed

## Error Handling

### TMDB Validation Failures
- Invalid key: Shows error toast, stays on TMDB step
- Network error: Shows warning, allows retry
- Success: Green toast, auto-advances

### Trakt Validation Failures
- Missing credentials: Error toast, stays on credentials step
- OAuth fails: Returns to OAuth step with error
- Network error: Shows warning, allows retry

### Final Validation Failures
- Redirects back to the step that failed
- Shows specific error message
- User can fix and retry

## Benefits

### For Users
1. **Clear Progress:** Visual stepper shows exactly where they are
2. **Error Prevention:** Validates each step before proceeding
3. **Helpful Instructions:** Links to create API keys, copy-paste helpers
4. **Confidence:** Final validation ensures everything works before proceeding

### For Developers
1. **Reliability:** App won't load without essential TMDB configuration
2. **Debugging:** Clear validation endpoint shows exactly what's missing
3. **Maintainability:** Centralized validation logic
4. **Flexibility:** Easy to add more required services in future

## Testing Checklist

- [ ] New user can complete full setup flow
- [ ] TMDB validation rejects invalid keys
- [ ] TMDB validation accepts valid keys
- [ ] Can navigate back between steps
- [ ] OAuth redirects work correctly
- [ ] Final validation catches missing credentials
- [ ] Dashboard only loads when fully configured
- [ ] Existing users with only Trakt see TMDB prompt
- [ ] Error messages are clear and actionable
- [ ] Progress indicator updates correctly

## Configuration Required

### Users Need:
1. **Trakt API Application**
   - Create at: https://trakt.tv/oauth/applications/new
   - Redirect URI: `http://localhost:5173/auth/trakt/callback`
   - Provides: Client ID, Client Secret

2. **TMDB API Key** ⭐ NEW REQUIREMENT
   - Get at: https://www.themoviedb.org/settings/api
   - Free for personal use
   - Provides: API Key (v3 auth)

## API Endpoints Summary

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/settings/trakt-credentials` | GET | Check if Trakt configured |
| `/api/settings/trakt-credentials` | POST | Save Trakt credentials |
| `/api/settings/tmdb-key` | GET | Check if TMDB configured |
| `/api/settings/tmdb-key` | POST | Save and validate TMDB key |
| `/api/settings/validate-setup` | GET | **NEW** - Validate all setup |
| `/api/trakt/status` | GET | Check OAuth status |
| `/api/trakt/oauth/url` | GET | Get OAuth redirect URL |

## Files Modified

### Backend
- `backend/app/api/settings.py` - Added `/validate-setup` endpoint

### Frontend
- `frontend/src/components/SetupScreen.tsx` - Complete redesign with 3-step flow

## Migration Notes

### Existing Users
- Users with only Trakt configured will see TMDB setup step on next login
- Dashboard will not load until TMDB key is configured
- Clear error messages guide them to TMDB setup

### New Users
- Must complete all 3 steps before accessing dashboard
- Cannot skip TMDB configuration
- Validation ensures everything works before proceeding

## Future Enhancements

- [ ] Add "Skip for now" option with limited functionality warning
- [ ] Email validation for TMDB account verification
- [ ] Automatic API key rotation reminders
- [ ] Multi-user support with per-user API keys
- [ ] Setup wizard walkthrough video/tutorial
- [ ] Automatic key validation on settings page
- [ ] Export/import configuration for easy setup on multiple devices
