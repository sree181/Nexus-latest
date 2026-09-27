package enterprise

import "encoding/asn1"

func asn1Unmarshal(input []byte, value any) ([]byte, error) {
	return asn1.Unmarshal(input, value)
}
