package protocol

import (
	"bytes"
	"encoding/hex"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

type manifestCase struct {
	Name        string   `yaml:"name"`
	MessageType string   `yaml:"message_type"`
	TaskIDHex   string   `yaml:"task_id_hex"`
	RequestID   uint64   `yaml:"request_id"`
	Flags       []string `yaml:"flags"`
	PayloadHex  string   `yaml:"payload_hex"`
	FrameHex    string   `yaml:"frame_hex"`
}

type manifest struct {
	Cases []manifestCase `yaml:"cases"`
}

func repoRoot(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("could not determine test file path")
	}
	// agent/pkg/protocol/messages_test.go -> repo root is four levels up.
	return filepath.Join(filepath.Dir(thisFile), "..", "..", "..")
}

func loadManifest(t *testing.T) manifest {
	t.Helper()
	path := filepath.Join(repoRoot(t), "testdata", "protocol", "v1", "manifest.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	var m manifest
	if err := yaml.Unmarshal(data, &m); err != nil {
		t.Fatalf("parse manifest: %v", err)
	}
	return m
}

var messageTypeByName = map[string]MessageType{
	"SUBMIT": MessageSubmit, "PULL": MessagePull, "TASK": MessageTask,
	"HEARTBEAT": MessageHeartbeat, "RESULT": MessageResult, "CANCEL": MessageCancel,
	"COMPLETE": MessageComplete, "STEAL": MessageSteal, "ACK": MessageAck,
	"STATUS": MessageStatus, "RESUME_RESULTS": MessageResumeResults, "ERROR": MessageError,
	"OBJECT_PUT": MessageObjectPut, "OBJECT_GET": MessageObjectGet, "OBJECT_CHUNK": MessageObjectChunk,
	"HELLO": MessageHello, "TASK_QUERY": MessageTaskQuery,
	"REGISTER_TASKS": MessageRegisterTasks,
}

func flagsFromNames(names []string) Flags {
	var f Flags
	for _, n := range names {
		switch strings.ToLower(n) {
		case "error":
			f |= FlagError
		case "idempotent":
			f |= FlagIdempotent
		case "forwarded":
			f |= FlagForwarded
		}
	}
	return f
}

func TestGoldenVectorFrameBytes(t *testing.T) {
	m := loadManifest(t)
	for _, c := range m.Cases {
		c := c
		t.Run(c.Name, func(t *testing.T) {
			mt, ok := messageTypeByName[c.MessageType]
			if !ok {
				t.Fatalf("unknown message type %q", c.MessageType)
			}
			payload, err := hex.DecodeString(c.PayloadHex)
			if err != nil {
				t.Fatal(err)
			}
			wantFrame, err := hex.DecodeString(c.FrameHex)
			if err != nil {
				t.Fatal(err)
			}
			taskIDBytes, err := hex.DecodeString(c.TaskIDHex)
			if err != nil {
				t.Fatal(err)
			}
			var taskID [16]byte
			copy(taskID[:], taskIDBytes)

			frame := Frame{
				Version: 1, MessageType: mt, TaskID: taskID, RequestID: c.RequestID,
				Flags: flagsFromNames(c.Flags), Payload: payload,
			}
			got, err := EncodeFrame(frame, maxPayload)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(got, wantFrame) {
				t.Fatalf("frame bytes mismatch\n got: %x\nwant: %x", got, wantFrame)
			}
		})
	}
}

func TestGoldenVectorPayloadRoundTrips(t *testing.T) {
	m := loadManifest(t)
	for _, c := range m.Cases {
		c := c
		t.Run(c.Name, func(t *testing.T) {
			mt, ok := messageTypeByName[c.MessageType]
			if !ok {
				t.Fatalf("unknown message type %q", c.MessageType)
			}
			payload, err := hex.DecodeString(c.PayloadHex)
			if err != nil {
				t.Fatal(err)
			}

			value, err := DecodePayload(mt, payload)
			if err != nil {
				t.Fatalf("decode: %v", err)
			}
			reEncoded, err := EncodePayload(mt, value)
			if err != nil {
				t.Fatalf("encode: %v", err)
			}
			if !bytes.Equal(reEncoded, payload) {
				t.Fatalf("payload round trip mismatch\n got: %x\nwant: %x", reEncoded, payload)
			}
		})
	}
}

type invalidMeta struct {
	ExpectedErrorCode string `yaml:"expected_error_code"`
	MaxPayloadBytes   uint32 `yaml:"max_payload_bytes"`
}

func TestInvalidCorpusFailsWithStableCode(t *testing.T) {
	dir := filepath.Join(repoRoot(t), "testdata", "protocol", "v1", "invalid")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".bin") {
			continue
		}
		name := strings.TrimSuffix(entry.Name(), ".bin")
		t.Run(name, func(t *testing.T) {
			data, err := os.ReadFile(filepath.Join(dir, entry.Name()))
			if err != nil {
				t.Fatal(err)
			}
			metaBytes, err := os.ReadFile(filepath.Join(dir, name+".yaml"))
			if err != nil {
				t.Fatal(err)
			}
			var meta invalidMeta
			if err := yaml.Unmarshal(metaBytes, &meta); err != nil {
				t.Fatal(err)
			}

			_, decodeErr := ReadFrame(bytes.NewReader(data), meta.MaxPayloadBytes)
			var got *DecodeError
			if decodeErr != nil {
				de, ok := decodeErr.(*DecodeError)
				if !ok {
					t.Fatalf("non-DecodeError: %v", decodeErr)
				}
				got = de
			} else {
				// Header decoded fine; the failure must be in the payload.
				frame, err := ReadFrame(bytes.NewReader(data), meta.MaxPayloadBytes)
				if err != nil {
					de, ok := err.(*DecodeError)
					if !ok {
						t.Fatalf("non-DecodeError: %v", err)
					}
					got = de
				} else {
					_, payloadErr := DecodePayload(frame.MessageType, frame.Payload)
					de, ok := payloadErr.(*DecodeError)
					if !ok {
						t.Fatalf("expected error decoding payload for case %q, got none", name)
					}
					got = de
				}
			}
			if got.Code != meta.ExpectedErrorCode {
				t.Fatalf("got code %q, want %q", got.Code, meta.ExpectedErrorCode)
			}
		})
	}
}

// -- canonical encoding profile ------------------------------------------

func TestMapKeysSortedAscendingUTF8(t *testing.T) {
	ref := &ObjectRef{Store: "s", Key: "k", Size: 1, SHA256: bytes.Repeat([]byte{0}, 32), Codec: "bytes"}
	encoded := ref.Encode()
	if encoded[0] != 0x85 {
		t.Fatalf("expected fixmap(5) header, got %#x", encoded[0])
	}
	decoded, err := unpackStrict(encoded)
	if err != nil {
		t.Fatal(err)
	}
	m, err := asMap(decoded, "payload")
	if err != nil {
		t.Fatal(err)
	}
	got, err := DecodeObjectRef(m)
	if err != nil {
		t.Fatal(err)
	}
	if got.Store != ref.Store || got.Key != ref.Key || got.Size != ref.Size || got.Codec != ref.Codec || !bytes.Equal(got.SHA256, ref.SHA256) {
		t.Fatalf("round trip mismatch: %+v vs %+v", got, ref)
	}
}

func TestFixedWidthUint64RegardlessOfValue(t *testing.T) {
	ref := &ObjectRef{Store: "s", Key: "k", Size: 0, SHA256: bytes.Repeat([]byte{0}, 32), Codec: "bytes"}
	encoded := ref.Encode()
	if !bytes.Contains(encoded, append(packStr("size"), 0xcf)) {
		t.Fatalf("expected fixed-width uint64 marker after size key: %x", encoded)
	}
}

func TestDuplicateKeyRejected(t *testing.T) {
	payload := append([]byte{0x82}, packStr("lease_id")...)
	payload = append(payload, packBin(make([]byte, 16))...)
	payload = append(payload, packStr("lease_id")...)
	payload = append(payload, packBin(make([]byte, 16))...)
	_, err := DecodePayload(MessageHeartbeat, payload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != InvalidMessage {
		t.Fatalf("got %v, want invalid_message", err)
	}
}

func TestUnknownKeyRejected(t *testing.T) {
	payload := append([]byte{0x82}, packStr("lease_id")...)
	payload = append(payload, packBin(make([]byte, 16))...)
	payload = append(payload, packStr("extra")...)
	payload = append(payload, packNil()...)
	_, err := DecodePayload(MessageHeartbeat, payload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != InvalidMessage {
		t.Fatalf("got %v, want invalid_message", err)
	}
}

func TestMissingRequiredKeyRejected(t *testing.T) {
	payload := []byte{0x80}
	_, err := DecodePayload(MessageHeartbeat, payload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != InvalidMessage {
		t.Fatalf("got %v, want invalid_message", err)
	}
}

func TestTrailingBytesRejected(t *testing.T) {
	req := &HeartbeatRequest{LeaseID: make([]byte, 16)}
	payload := append(req.Encode(), 0x00)
	_, err := DecodePayload(MessageHeartbeat, payload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != InvalidMessage {
		t.Fatalf("got %v, want invalid_message", err)
	}
}

func TestBadIDSizeRejected(t *testing.T) {
	payload := append([]byte{0x81}, packStr("lease_id")...)
	payload = append(payload, packBin(make([]byte, 15))...)
	_, err := DecodePayload(MessageHeartbeat, payload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != InvalidMessage {
		t.Fatalf("got %v, want invalid_message", err)
	}
}

func TestValueRefRequiresExactlyOneBranch(t *testing.T) {
	v := &ValueRef{}
	if err := v.Validate(); err == nil {
		t.Fatal("expected error for empty ValueRef")
	}
	both := &ValueRef{Inline: []byte("x"), Codec: "msgpack", Object: &ObjectRef{}}
	if err := both.Validate(); err == nil {
		t.Fatal("expected error for both branches set")
	}
}

func TestCompletionRequiresExactlyOneOfResultOrFailure(t *testing.T) {
	c := &Completion{LeaseID: make([]byte, 16)}
	if _, err := c.Encode(); err == nil {
		t.Fatal("expected error when neither result nor failure is set")
	}
	c2 := &Completion{
		LeaseID: make([]byte, 16),
		Result:  &ObjectRef{Store: "s", Key: "k", SHA256: make([]byte, 32), Codec: "bytes"},
		Failure: &Failure{Code: "task_exception", Message: "x"},
	}
	if _, err := c2.Encode(); err == nil {
		t.Fatal("expected error when both result and failure are set")
	}
}

func TestAckRequiresExactFieldSetForKind(t *testing.T) {
	if _, err := NewAck("submit", map[string]interface{}{}); err == nil {
		t.Fatal("expected error for missing required field")
	}
	if _, err := NewAck("hello", map[string]interface{}{"task_id": make([]byte, 16)}); err == nil {
		t.Fatal("expected error for extra field")
	}
}

func TestEncodePayloadRejectsWrongTypeForMessage(t *testing.T) {
	req := &PullRequest{WorkerID: "w", CapabilityGeneration: 1}
	_, err := EncodePayload(MessageHeartbeat, req)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != InvalidMessage {
		t.Fatalf("got %v, want invalid_message", err)
	}
}
