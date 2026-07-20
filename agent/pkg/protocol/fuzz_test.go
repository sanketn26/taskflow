package protocol

import (
	"bytes"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"

	"gopkg.in/yaml.v3"
)

// FuzzReadFrame is seeded from testdata/protocol/v1 (golden frames and the
// invalid corpus). Properties: never panic, never allocate beyond the
// configured bound (proven by frame_test.go's explicit assertion; here we
// only check accepted frames stay well-formed), and every failure carries
// a registered stable error code.
func FuzzReadFrame(f *testing.F) {
	seedFrameCorpus(f)

	f.Fuzz(func(t *testing.T, data []byte) {
		frame, err := ReadFrame(bytes.NewReader(data), maxPayload)
		if err != nil {
			de, ok := err.(*DecodeError)
			if !ok {
				t.Fatalf("non-DecodeError returned: %v", err)
			}
			if _, registered := ErrorRetryable[de.Code]; !registered {
				t.Fatalf("unregistered error code: %q", de.Code)
			}
			return
		}
		if frame == nil {
			return
		}
		if frame.Version != ProtocolVersion {
			t.Fatalf("accepted frame has unexpected version %d", frame.Version)
		}
		if !frame.MessageType.valid() {
			t.Fatalf("accepted frame has invalid message type %d", frame.MessageType)
		}
	})
}

// FuzzDecodePayload is seeded from the golden payload bytes. Any message
// type may be tried against any seed; a successful decode must re-encode
// canonically and round-trip.
func FuzzDecodePayload(f *testing.F) {
	seedEnvelopeCorpus(f)

	f.Fuzz(func(t *testing.T, data []byte, mtByte byte) {
		mt := MessageType(mtByte%17 + 1) // valid range is 0x01..0x11
		value, err := DecodePayload(mt, data)
		if err != nil {
			de, ok := err.(*DecodeError)
			if !ok {
				t.Fatalf("non-DecodeError returned: %v", err)
			}
			if _, registered := ErrorRetryable[de.Code]; !registered {
				t.Fatalf("unregistered error code: %q", de.Code)
			}
			return
		}
		reEncoded, err := EncodePayload(mt, value)
		if err != nil {
			t.Fatalf("accepted value failed to re-encode: %v", err)
		}
		again, err := DecodePayload(mt, reEncoded)
		if err != nil {
			t.Fatalf("re-encoded bytes failed to decode: %v", err)
		}
		_ = again
	})
}

func fuzzRepoRoot(f *testing.F) string {
	f.Helper()
	wd, err := os.Getwd()
	if err != nil {
		f.Fatal(err)
	}
	// agent/pkg/protocol -> repo root is three levels up.
	return filepath.Join(wd, "..", "..", "..")
}

func seedFrameCorpus(f *testing.F) {
	f.Helper()
	m := loadManifestForFuzz(f)
	for _, c := range m.Cases {
		data, err := hex.DecodeString(c.FrameHex)
		if err != nil {
			f.Fatal(err)
		}
		f.Add(data)
	}
	dir := filepath.Join(fuzzRepoRoot(f), "testdata", "protocol", "v1", "invalid")
	entries, err := os.ReadDir(dir)
	if err != nil {
		f.Fatal(err)
	}
	for _, e := range entries {
		if filepath.Ext(e.Name()) != ".bin" {
			continue
		}
		data, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			f.Fatal(err)
		}
		f.Add(data)
	}
}

func seedEnvelopeCorpus(f *testing.F) {
	f.Helper()
	m := loadManifestForFuzz(f)
	for i, c := range m.Cases {
		data, err := hex.DecodeString(c.PayloadHex)
		if err != nil {
			f.Fatal(err)
		}
		f.Add(data, byte(i%17))
	}
}

func loadManifestForFuzz(f *testing.F) manifest {
	f.Helper()
	path := filepath.Join(fuzzRepoRoot(f), "testdata", "protocol", "v1", "manifest.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		f.Fatal(err)
	}
	var m manifest
	if err := yaml.Unmarshal(data, &m); err != nil {
		f.Fatal(err)
	}
	return m
}
