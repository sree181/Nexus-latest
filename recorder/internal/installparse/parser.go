package installparse

import (
	"path/filepath"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

type Target struct {
	Name      string `json:"name"`
	Version   string `json:"version"`
	Ecosystem string `json:"ecosystem"`
	Manager   string `json:"manager"`
	Exact     bool   `json:"exact"`
}

var (
	pypiName     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)
	npmName      = regexp.MustCompile(`^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*$`)
	exactVersion = regexp.MustCompile(`^[0-9][A-Za-z0-9._+\-]*$`)
	assignment   = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*=.*$`)
	python3      = regexp.MustCompile(`^python3(?:\.\d+)?$`)
)

var optionsWithValue = map[string]bool{
	"-c": true, "--constraint": true, "-e": true, "--editable": true,
	"-f": true, "--find-links": true, "-i": true, "--index-url": true,
	"--extra-index-url": true, "--proxy": true, "--retries": true,
	"--timeout": true, "--trusted-host": true, "--target": true,
	"--platform": true, "--python-version": true, "--implementation": true,
	"--abi": true, "--root": true, "--prefix": true, "--cache-dir": true,
	"--registry": true, "--tag": true, "--workspace": true, "--filter": true,
	"--cwd": true, "--directory": true, "--source": true, "--group": true,
	"--python": true, "--extras": true,
}

var indirectOptions = map[string]bool{"-r": true, "--requirement": true}

func Parse(command string) []Target {
	found := make([]Target, 0)
	seen := map[string]bool{}
	for _, segment := range segments(command) {
		manager, ecosystem, rest, ok := grammar(unwrap(segment))
		if !ok {
			continue
		}
		for _, operand := range operands(rest) {
			name, version, exact, ok := parseOperand(manager, ecosystem, operand)
			if !ok || utf8.RuneCountInString(name) > 256 || utf8.RuneCountInString(version) > 128 {
				continue
			}
			key := ecosystem + "\x1f" + strings.ToLower(name) + "\x1f" + version + "\x1f" + manager
			if seen[key] {
				continue
			}
			seen[key] = true
			found = append(found, Target{Name: name, Version: version, Ecosystem: ecosystem, Manager: manager, Exact: exact})
		}
	}
	return found
}

func Pinned(command string) []Target {
	pinned := make([]Target, 0)
	for _, target := range Parse(command) {
		if target.Exact {
			pinned = append(pinned, target)
		}
	}
	return pinned
}

func segments(command string) [][]string {
	var out [][]string
	var current []string
	var token strings.Builder
	var quote rune
	escaped := false
	flushToken := func() {
		if token.Len() > 0 {
			current = append(current, token.String())
			token.Reset()
		}
	}
	flushSegment := func() {
		flushToken()
		if len(current) > 0 {
			out = append(out, current)
			current = nil
		}
	}
	for _, r := range command {
		if escaped {
			token.WriteRune(r)
			escaped = false
			continue
		}
		if r == '\\' && quote != '\'' {
			escaped = true
			continue
		}
		if quote != 0 {
			if r == quote {
				quote = 0
			} else {
				token.WriteRune(r)
			}
			continue
		}
		if r == '\'' || r == '"' {
			quote = r
			continue
		}
		if strings.ContainsRune(";&|<>()", r) {
			flushSegment()
			continue
		}
		if unicode.IsSpace(r) {
			flushToken()
			continue
		}
		token.WriteRune(r)
	}
	if quote != 0 || escaped {
		return nil
	}
	flushSegment()
	return out
}

func unwrap(tokens []string) []string {
	found := append([]string(nil), tokens...)
	for len(found) > 0 {
		executable := filepath.Base(found[0])
		switch executable {
		case "command":
			found = found[1:]
			continue
		case "env":
			found = found[1:]
			for len(found) > 0 && (assignment.MatchString(found[0]) || strings.HasPrefix(found[0], "-")) {
				found = found[1:]
			}
			continue
		case "sudo":
			found = found[1:]
			for len(found) > 0 && strings.HasPrefix(found[0], "-") {
				option := found[0]
				found = found[1:]
				if map[string]bool{"-u": true, "-g": true, "-h": true, "-p": true, "-C": true, "-T": true, "-R": true}[option] && len(found) > 0 {
					found = found[1:]
				}
			}
			continue
		}
		for len(found) > 0 && assignment.MatchString(found[0]) {
			found = found[1:]
		}
		break
	}
	return found
}

func grammar(tokens []string) (string, string, []string, bool) {
	if len(tokens) == 0 {
		return "", "", nil, false
	}
	executable := filepath.Base(tokens[0])
	rest := tokens[1:]
	isPython := executable == "python" || executable == "python3" || python3.MatchString(executable)
	if isPython && len(rest) >= 3 && rest[0] == "-m" && rest[1] == "pip" && rest[2] == "install" {
		return "pip", "PyPI", rest[3:], true
	}
	if (executable == "pip" || executable == "pip3") && len(rest) >= 1 && rest[0] == "install" {
		return "pip", "PyPI", rest[1:], true
	}
	if executable == "uv" && len(rest) >= 2 && rest[0] == "pip" && rest[1] == "install" {
		return "uv", "PyPI", rest[2:], true
	}
	if executable == "uv" && len(rest) >= 1 && rest[0] == "add" {
		return "uv", "PyPI", rest[1:], true
	}
	if executable == "poetry" && len(rest) >= 1 && rest[0] == "add" {
		return "poetry", "PyPI", rest[1:], true
	}
	if executable == "npm" && len(rest) >= 1 && (rest[0] == "install" || rest[0] == "i") {
		return "npm", "npm", rest[1:], true
	}
	if executable == "yarn" && len(rest) >= 1 && rest[0] == "add" {
		return "yarn", "npm", rest[1:], true
	}
	return "", "", nil, false
}

func operands(tokens []string) []string {
	var out []string
	skip := false
	literal := false
	for _, token := range tokens {
		if skip {
			skip = false
			continue
		}
		if token == "--" {
			literal = true
			continue
		}
		if !literal && (indirectOptions[token] || optionsWithValue[token]) {
			skip = true
			continue
		}
		if !literal && strings.HasPrefix(token, "--") && strings.Contains(token, "=") {
			continue
		}
		if !literal && strings.HasPrefix(token, "-") {
			continue
		}
		out = append(out, token)
	}
	return out
}

func parseOperand(manager, ecosystem, spec string) (string, string, bool, bool) {
	if ecosystem == "npm" {
		return parseNPM(spec)
	}
	if manager == "poetry" {
		return parsePoetry(spec)
	}
	return parsePyPI(spec)
}

func parsePyPI(spec string) (string, string, bool, bool) {
	if containsUnsafe(spec, true) {
		return "", "", false, false
	}
	raw := strings.TrimSpace(strings.SplitN(spec, ";", 2)[0])
	for _, separator := range []string{"===", "=="} {
		if strings.Contains(raw, separator) {
			parts := strings.SplitN(raw, separator, 2)
			name := strings.SplitN(parts[0], "[", 2)[0]
			if pypiName.MatchString(name) && exactVersion.MatchString(parts[1]) {
				return name, parts[1], true, true
			}
			return "", "", false, false
		}
	}
	name := regexp.MustCompile(`[<>=!~]`).Split(raw, 2)[0]
	name = strings.SplitN(name, "[", 2)[0]
	if pypiName.MatchString(name) {
		return name, "", false, true
	}
	return "", "", false, false
}

func parsePoetry(spec string) (string, string, bool, bool) {
	if index := strings.LastIndex(spec, "@"); index >= 0 {
		name, version := spec[:index], spec[index+1:]
		if pypiName.MatchString(name) && exactVersion.MatchString(version) {
			return name, version, true, true
		}
		if pypiName.MatchString(name) {
			return name, "", false, true
		}
		return "", "", false, false
	}
	return parsePyPI(spec)
}

func parseNPM(spec string) (string, string, bool, bool) {
	if containsUnsafe(spec, false) {
		return "", "", false, false
	}
	name, version := spec, ""
	if strings.HasPrefix(spec, "@") {
		if split := strings.LastIndex(spec, "@"); split > strings.Index(spec, "/") {
			name, version = spec[:split], spec[split+1:]
		}
	} else if split := strings.LastIndex(spec, "@"); split >= 0 {
		name, version = spec[:split], spec[split+1:]
	}
	if !npmName.MatchString(name) {
		return "", "", false, false
	}
	exact := version != "" && exactVersion.MatchString(version)
	if !exact {
		version = ""
	}
	return name, version, exact, true
}

func containsUnsafe(spec string, slashUnsafe bool) bool {
	unsafe := []string{"\\", "://", "${", "$(", "`"}
	if slashUnsafe {
		unsafe = append(unsafe, "/")
	}
	for _, mark := range unsafe {
		if strings.Contains(spec, mark) {
			return true
		}
	}
	return false
}
