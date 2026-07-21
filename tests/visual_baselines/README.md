# Qt visual regression baselines

These PNGs cover Wisp's five critical desktop surfaces on Windows: onboarding,
settings, chat, agent task setup, and the speech bubble. The test fixes the Qt
style, font, scale, language, theme, sizes, and sample content before capturing.

To intentionally refresh the baselines on Windows after reviewing a UI change:

```powershell
$env:WISP_UPDATE_VISUAL_BASELINES = "1"
.\.venv\Scripts\python.exe -m pytest -q tests\test_visual_regression.py
Remove-Item Env:\WISP_UPDATE_VISUAL_BASELINES
```

Review every changed PNG before committing it. Linux skips this suite because
font rasterization differs enough across distributions to create noisy failures;
the normal cross-platform UI behavior tests still run there.
