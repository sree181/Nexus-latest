package canonicaljson

import (
	"bytes"
	"encoding/json"
)

// Marshal matches Python's json.dumps(sort_keys=True, separators=(",", ":"),
// ensure_ascii=False) for the JSON values used by the recorder protocol.
func Marshal(value any) ([]byte, error) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(buffer.Bytes(), []byte("\n")), nil
}
