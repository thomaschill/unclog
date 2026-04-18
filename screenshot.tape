# README hero screenshot — captures the --report view at full render.
#
# Run with: vhs screenshot.tape
# Output:   screenshot.png

Output screenshot.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1400
Set Height 500
Set Padding 20
Set TypingSpeed 40ms

Env PATH "/Users/tomhill/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

Hide
Type "clear"
Enter
Sleep 500ms
Show

Sleep 300ms
Type "unclog --report"
Sleep 400ms
Enter

# Let scan + MCP probe + render fully settle
Sleep 7s

Screenshot screenshot.png
