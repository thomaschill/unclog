# unclog demo — interactive fix flow
#
# Run with: vhs demo.tape
# Output:   demo.gif
#
# Drives the picker to toggle a couple of findings, submits,
# then declines both confirm prompts ('n'). Nothing is written.

Output demo.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1400
Set Height 1000
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

# Wait for scan + MCP probe + render + picker mount
Sleep 8s

# Move cursor down through rows so the viewer sees the highlight travel
Down
Sleep 350ms
Down
Sleep 350ms

# Toggle one — footer updates to "1 selected"
Space
Sleep 700ms

# Move further, toggle another — footer updates to "2 selected"
Down
Sleep 300ms
Down
Sleep 350ms
Space
Sleep 900ms

# Submit the picker selection
Enter
Sleep 1200ms

# Step 1 confirm: "Apply N change(s)? [y/N]" — decline
Type "n"
Sleep 300ms
Enter
Sleep 900ms

# Step 2 curate confirm: "Review N agent(s) + skill(s) + remote MCP(s)…? [y/N]" — decline
Type "n"
Sleep 300ms
Enter

Sleep 700ms
