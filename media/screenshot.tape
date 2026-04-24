# README hero screenshot — captures the slim default view with the
# sectioned picker mounted.
#
# Run with: cd media && vhs screenshot.tape
# Output:   media/screenshot.png (relative to repo root)

# vhs requires an Output directive; route the throwaway GIF to /tmp
# since we only want the PNG for the README hero.
Output "/tmp/unclog-screenshot-throwaway.gif"

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

Screenshot screenshot.png
