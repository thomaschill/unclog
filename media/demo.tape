# unclog demo — interactive curate flow
#
# Run with: cd media && vhs demo.tape
# Output:   media/demo.gif (relative to repo root)
#
# Drives the single sectioned picker: scrolls a few rows, toggles a
# few items, submits, and declines the "Delete N item(s)?" confirm.
# Nothing is written to disk.

Output "demo.gif"

Set Shell "zsh"
Set FontSize 14
Set Width 960
Set Height 640
Set Padding 20
Set TypingSpeed 40ms

Env PATH "/Users/tomhill/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

Hide
Type "clear"
Enter
Sleep 500ms
Show

Sleep 300ms
Type "unclog"
Sleep 400ms
Enter

# Wait for scan + render + picker mount (no probe in 0.2)
Sleep 3s

# Move cursor down through the picker — viewer sees highlight travel
Down
Sleep 350ms
Down
Sleep 350ms
Down
Sleep 350ms

# Toggle one row — running-total footer ticks to "1 selected"
Space
Sleep 700ms

Down
Sleep 300ms
Down
Sleep 350ms

# Toggle a second row — "2 selected"
Space
Sleep 700ms

Down
Sleep 300ms
Down
Sleep 300ms
Down
Sleep 350ms

# Toggle a third row — "3 selected"
Space
Sleep 900ms

# Submit the picker selection
Enter
Sleep 1200ms

# "Delete N item(s)? This cannot be undone. [y/N]" — decline; nothing is written.
Type "n"
Sleep 300ms
Enter

Sleep 900ms
