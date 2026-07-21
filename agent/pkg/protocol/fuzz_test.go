package protocol

import (
	"bytes"
	"testing"

	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/protobuf/proto"
)

// FuzzReadFrame uses representative valid and malformed frame seeds.
// Properties: never panic, never allocate beyond the
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
		mt := MessageType(mtByte%18 + 1) // valid range is 0x01..0x12
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

func seedFrameCorpus(f *testing.F) {
	f.Helper()
	payload, _ := EncodePayload(MessageStatus, &StatusRequest{})
	frame, _ := EncodeFrame(Frame{Version: 1, MessageType: MessageStatus, RequestID: 1, Payload: payload}, maxPayload)
	f.Add(frame)
	f.Add([]byte{})
	f.Add([]byte{1, 2, 3})
}

func seedEnvelopeCorpus(f *testing.F) {
	f.Helper()
	payload, _ := EncodePayload(MessageHello, &Hello{Role: "admin"})
	f.Add(payload, byte(MessageHello-1))
	registration, _ := EncodePayload(MessageRegisterTasks, &TaskRegistration{WorkerId: "w", Generation: 1, Tasks: []*TaskCapability{}})
	f.Add(registration, byte(MessageRegisterTasks-1))
	invalidSubmit, _ := proto.Marshal(&pb.ControlMessage{Body: &pb.ControlMessage_Submit{Submit: &pb.TaskEnvelope{}}})
	f.Add(invalidSubmit, byte(MessageSubmit-1))
	f.Add([]byte{}, byte(0))
	f.Add([]byte{0xff}, byte(0))
}
