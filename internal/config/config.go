// Package config reads controller settings from environment variables.
//
// There is no configuration file: Kubernetes supplies settings through
// ConfigMaps and Secrets. Errors name the variable and never echo its value,
// because the backend URL carries credentials.
package config

import (
	"fmt"
	"os"
	"regexp"
	"strings"
)

const (
	BackendURL     = "TASKWIRE_BACKEND_URL"
	StartupCheck   = "TASKWIRE_STARTUP_CHECK"
	AllowEphemeral = "TASKWIRE_ALLOW_EPHEMERAL"
)

// Error is a missing or invalid setting. Its message never contains the value.
type Error struct{ Variable, Problem string }

func (e *Error) Error() string { return e.Variable + ": " + e.Problem }

// Check is how much of the backend contract the controller verifies at start.
type Check string

const (
	CheckFull     Check = "full"
	CheckSettings Check = "settings"
	CheckOff      Check = "off"
)

// Settings are the validated controller settings.
type Settings struct {
	backendURL string // unexported so it cannot be printed by accident
	// StartupCheck is "" when not configured; the caller applies its default.
	StartupCheck   Check
	AllowEphemeral bool
}

func (s Settings) BackendURL() string         { return s.backendURL }
func (s Settings) RedactedBackendURL() string { return RedactURL(s.backendURL) }

// BackendKind is "postgresql" or "memory".
func (s Settings) BackendKind() string { return backendSchemes[scheme(s.backendURL)] }

// String never includes the backend URL.
func (s Settings) String() string {
	return fmt.Sprintf("Settings{backend=%s startup_check=%q allow_ephemeral=%t}",
		s.BackendKind(), s.StartupCheck, s.AllowEphemeral)
}

var (
	backendSchemes = map[string]string{"postgresql": "postgresql", "postgres": "postgresql", "memory": "memory"}
	secretQuery    = regexp.MustCompile(`(?i)\b(password|sslpassword)=[^&#]*`)
	hostPattern    = regexp.MustCompile(`^(\[[^\]]*\]|[^:\[\]]*)(:\d*)?$`)
)

// Load reads settings from the process environment.
func Load() (Settings, error) { return LoadFrom(os.LookupEnv) }

// LoadFrom reads settings using lookup (for tests).
func LoadFrom(lookup func(string) (string, bool)) (Settings, error) {
	get := func(name string) string { v, _ := lookup(name); return strings.TrimSpace(v) }
	var s Settings

	raw := get(BackendURL)
	if raw == "" {
		return s, &Error{BackendURL, "is required"}
	}
	sch := scheme(raw)
	if _, ok := backendSchemes[sch]; !ok {
		return s, &Error{BackendURL, "must start with one of: memory://, postgres://, postgresql://"}
	}
	if sch == "memory" && raw != "memory://" {
		return s, &Error{BackendURL, "memory backend takes no address: use memory://"}
	}
	if sch != "memory" {
		if err := checkHosts(raw); err != nil {
			return s, err
		}
	}
	s.backendURL = raw

	switch c := Check(strings.ToLower(get(StartupCheck))); c {
	case "", CheckFull, CheckSettings, CheckOff:
		s.StartupCheck = c
	default:
		return s, &Error{StartupCheck, "must be one of: full, settings, off"}
	}

	switch strings.ToLower(get(AllowEphemeral)) {
	case "":
	case "1", "true", "yes", "on":
		s.AllowEphemeral = true
	case "0", "false", "no", "off":
	default:
		return s, &Error{AllowEphemeral, "must be a boolean: true or false"}
	}
	return s, nil
}

// RedactURL replaces any password (userinfo or query) with ***. It never
// fails and fails closed: everything between :// and the last @ is userinfo,
// so a password with an unencoded / # or ? is still hidden.
func RedactURL(url string) string {
	sch, rest, ok := strings.Cut(url, "://")
	if !ok {
		return url
	}
	if at := strings.LastIndex(rest, "@"); at != -1 {
		if user, _, hasPass := strings.Cut(rest[:at], ":"); hasPass {
			rest = user + ":***" + rest[at:]
		}
	}
	return secretQuery.ReplaceAllString(sch+"://"+rest, "${1}=***")
}

func scheme(url string) string {
	sch, _, ok := strings.Cut(url, "://")
	if !ok {
		return ""
	}
	return strings.ToLower(sch)
}

func authorityEnd(rest string) int {
	if i := strings.IndexAny(rest, "/?#"); i != -1 {
		return i
	}
	return len(rest)
}

func checkHosts(raw string) error {
	_, rest, _ := strings.Cut(raw, "://")
	end := authorityEnd(rest)
	rest = rest[strings.LastIndex(rest[:end], "@")+1:]
	end = authorityEnd(rest)
	for _, host := range strings.Split(rest[:end], ",") {
		if !hostPattern.MatchString(host) {
			return &Error{BackendURL, "has a malformed host or port"}
		}
	}
	return nil
}
