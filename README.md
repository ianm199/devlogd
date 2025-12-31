## Devlog 
I created this tool so that when using coding agents like Claude Code or codex I wanted the agents to able to launch frontends, read console logs, run commands in the console, 
view network calls, read the DOM etc. This lets coding agents go deeper on issues without having to do lots of copy and pasting. In general, it's very important to close
the feedback loop on these kind of things. This tool is mean to be highly extensible so I have found it helpful to when an agent isn't debugging something effectively but 
it gets the answer eventually have it give feedback on where the tool is lacking. That is how some of the tools in this came along like the iframe specific tool
