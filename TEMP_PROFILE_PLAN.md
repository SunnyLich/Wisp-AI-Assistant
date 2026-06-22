# Temporary Profile Implementation Plan

## Goal

Add settings profiles without turning the normal UI into a wall of knobs. Profiles own behavior; credentials remain shared.

## Product Contract

- API keys and OAuth/session state stay global.
- A profile owns model choice, fallbacks, context modes, tool budgets, context budgets, and local file behavior.
- Caller/hotkey entries select a profile, then keep only entry-point details such as hotkey, paste-back, and intent labels.
- Existing global `.env` keys continue to work as the default profile for compatibility.
- The first implementation should expose the core profile contract in config and tests before adding a larger UI for profile editing.

## Implementation Steps

1. Define profile-shaped settings data in `core/settings_model.py`.
2. Add default profiles and `.env` loading in `config.py`.
3. Apply the active/default profile to exported module-level settings so existing model and context code keeps working.
4. Let caller and voice rows carry a `profile` id, defaulting to the active profile.
5. Add helper functions for resolving a caller's effective profile and budgets.
6. Add focused tests for:
   - shared/global auth keys are not profile-scoped,
   - active profile overrides model/context budgets,
   - caller profile selection overrides the active profile for context/tool behavior.

## Out Of Scope For This Slice

- Full profile editor UI.
- Migration UI for existing presets.
- Per-profile encrypted credential stores.
- Per-source advanced budget widgets.
