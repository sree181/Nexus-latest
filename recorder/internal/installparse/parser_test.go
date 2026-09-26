package installparse

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type parserFixture struct {
	Name     string   `json:"name"`
	Command  string   `json:"command"`
	Expected []Target `json:"expected"`
}

func TestCompatibilityCorpus(t *testing.T) {
	body, err := os.ReadFile(filepath.Join("..", "..", "testdata", "package_commands.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []parserFixture
	if err := json.Unmarshal(body, &fixtures); err != nil {
		t.Fatal(err)
	}
	for _, fixture := range fixtures {
		fixture := fixture
		t.Run(fixture.Name, func(t *testing.T) {
			if got := Parse(fixture.Command); !reflect.DeepEqual(got, fixture.Expected) {
				t.Fatalf("Parse(%q) = %#v, want %#v", fixture.Command, got, fixture.Expected)
			}
		})
	}
}

func TestPinnedReturnsOnlyExactTargets(t *testing.T) {
	got := Pinned("pip install requests numpy==1.26.4 && npm i react@18.3.1 react@latest")
	want := []Target{
		{Name: "numpy", Version: "1.26.4", Ecosystem: "PyPI", Manager: "pip", Exact: true},
		{Name: "react", Version: "18.3.1", Ecosystem: "npm", Manager: "npm", Exact: true},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v, want %#v", got, want)
	}
}

func TestParseRejectsOversizedPackageFields(t *testing.T) {
	if got := Parse("pip install " + strings.Repeat("a", 257) + "==1.0.0"); len(got) != 0 {
		t.Fatalf("oversized package accepted: %#v", got)
	}
	if got := Parse("npm install demo@1" + strings.Repeat("0", 128)); len(got) != 0 {
		t.Fatalf("oversized version accepted: %#v", got)
	}
}
