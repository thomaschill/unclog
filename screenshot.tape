# README hero screenshot — captures the slim default view with the
# sectioned picker mounted.
#
# Run with: vhs screenshot.tape
# Output:   screenshot.png

# vhs requires an Output directive; route the throwaway GIF to /tmp
# since we only want the PNG for the README hero.
Output /tmp/unclog-screenshot-throwaway.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1400
Set Height 800
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

Screenshot screenshot.png
