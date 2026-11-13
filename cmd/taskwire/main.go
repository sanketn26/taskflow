// Command taskwire is the controller and its operator commands.
package main

import (
	"fmt"
	"io"
	"os"

	"github.com/sanketn26/taskflow/internal/version"
)

// planned subcommands: name -> (help, phase that implements it).
var planned = map[string]struct {
	help  string
	phase int
}{
	"migrate":    {"Install or upgrade the backend schema", 1},
	"check":      {"Verify the backend meets the Taskwire contract", 1},
	"controller": {"Run the controller", 2},
}

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "usage: taskwire <version|migrate|check|controller>")
		return 2
	}
	if args[0] == "version" {
		fmt.Fprintln(stdout, version.Version)
		return 0
	}
	if p, ok := planned[args[0]]; ok {
		fmt.Fprintf(stderr, "taskwire %s: not implemented yet (planned for Phase %d)\n", args[0], p.phase)
		return 1
	}
	fmt.Fprintf(stderr, "taskwire: unknown command %q\n", args[0])
	return 2
}
