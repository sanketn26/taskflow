package protocol

import (
	"math"
	"testing"
)

func TestPortableValueCanonicalRoundTrip(t *testing.T) {
	value := map[string]interface{}{"z": []interface{}{nil, true, int64(-33), uint64(math.MaxUint64), 1.5}, "a": []byte("x")}
	encoded, err := EncodePortableValue(value)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) < 3 || string(encoded[1:3]) != "\xa1a" {
		t.Fatalf("map keys not canonical: %x", encoded)
	}
	if _, err := DecodePortableValue(encoded); err != nil {
		t.Fatal(err)
	}
}

func TestPortableValueRejectsUnsupportedValues(t *testing.T) {
	for _, value := range []interface{}{math.NaN(), math.Inf(1), struct{}{}, map[int]string{1: "x"}} {
		if _, err := EncodePortableValue(value); err == nil {
			t.Fatalf("expected rejection for %T", value)
		}
	}
}
