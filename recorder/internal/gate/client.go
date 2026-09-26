package gate

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"
)

type Request struct {
	Package   string `json:"package"`
	Version   string `json:"version"`
	Ecosystem string `json:"ecosystem"`
	Session   string `json:"session,omitempty"`
}

type Advisory struct {
	ID            string   `json:"id"`
	Severity      string   `json:"severity"`
	Summary       string   `json:"summary"`
	CWE           *string  `json:"cwe,omitempty"`
	FixedVersions []string `json:"fixed_versions"`
	References    []string `json:"references"`
}

type Decision struct {
	Package     string     `json:"package"`
	Version     string     `json:"version"`
	Ecosystem   string     `json:"ecosystem"`
	Verdict     string     `json:"verdict"`
	Reasons     []string   `json:"reasons"`
	Advisories  []Advisory `json:"advisories"`
	Worst       *string    `json:"worst,omitempty"`
	Unavailable *string    `json:"unavailable,omitempty"`
	Policy      string     `json:"policy"`
	FleetAgents int        `json:"fleet_agents"`
	Sample      bool       `json:"sample"`
}

type Client struct {
	Origin      string
	DeviceToken string
	LocalUser   string
	Identity    func() (deviceToken, localUser string)
	HTTP        *http.Client
}

func (c Client) Ask(ctx context.Context, requestBody Request) (*Decision, error) {
	encoded, err := json.Marshal(requestBody)
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimSuffix(c.Origin, "/")+"/api/gate/package", bytes.NewReader(encoded))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	token, user := c.DeviceToken, c.LocalUser
	if c.Identity != nil {
		token, user = c.Identity()
	}
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	} else if user != "" {
		request.Header.Set("X-MeshAgent-User", user)
	}
	client := c.HTTP
	if client == nil {
		client = &http.Client{Timeout: 4500 * time.Millisecond}
	}
	httpClient := *client
	httpClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 1024*1024+1))
	if err != nil {
		return nil, err
	}
	if len(body) > 1024*1024 {
		return nil, fmt.Errorf("gate response exceeds 1 MiB")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("gate returned HTTP %d", response.StatusCode)
	}
	var decision Decision
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&decision); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("gate response contains more than one JSON value")
		}
		return nil, err
	}
	if err := validateDecision(requestBody, decision); err != nil {
		return nil, err
	}
	return &decision, nil
}

func validateDecision(request Request, decision Decision) error {
	if decision.Package != request.Package || decision.Version != request.Version || decision.Ecosystem != request.Ecosystem {
		return fmt.Errorf("gate response does not match request")
	}
	if !oneOf(decision.Ecosystem, "PyPI", "npm") || !oneOf(decision.Verdict, "allow", "warn", "block", "unknown") {
		return fmt.Errorf("gate response has an invalid enum")
	}
	if !within(decision.Package, 1, 256) || !within(decision.Version, 0, 128) || !within(decision.Policy, 0, 4096) || len(decision.Reasons) > 32 || len(decision.Advisories) > 100 {
		return fmt.Errorf("gate response exceeds contract bounds")
	}
	if decision.Worst != nil && !oneOf(*decision.Worst, "critical", "high", "medium", "low", "unknown") {
		return fmt.Errorf("gate response has an invalid severity")
	}
	if decision.Unavailable != nil && !within(*decision.Unavailable, 0, 2048) {
		return fmt.Errorf("gate response exceeds contract bounds")
	}
	for _, reason := range decision.Reasons {
		if !within(reason, 0, 4096) {
			return fmt.Errorf("gate response exceeds contract bounds")
		}
	}
	for _, advisory := range decision.Advisories {
		if !within(advisory.ID, 1, 256) || !within(advisory.Summary, 1, 2048) || !oneOf(advisory.Severity, "critical", "high", "medium", "low", "unknown") || len(advisory.FixedVersions) > 20 || len(advisory.References) > 10 {
			return fmt.Errorf("gate response advisory is invalid")
		}
		if advisory.CWE != nil && !within(*advisory.CWE, 0, 128) {
			return fmt.Errorf("gate response advisory is invalid")
		}
		for _, version := range advisory.FixedVersions {
			if !within(version, 0, 128) {
				return fmt.Errorf("gate response advisory is invalid")
			}
		}
		for _, reference := range advisory.References {
			if !within(reference, 0, 2048) {
				return fmt.Errorf("gate response advisory is invalid")
			}
		}
	}
	return nil
}

func within(value string, minimum, maximum int) bool {
	length := utf8.RuneCountInString(value)
	return length >= minimum && length <= maximum
}

func oneOf(value string, allowed ...string) bool {
	for _, candidate := range allowed {
		if value == candidate {
			return true
		}
	}
	return false
}
