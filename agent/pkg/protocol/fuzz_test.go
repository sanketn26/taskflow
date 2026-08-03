package protocol

import (
	"testing"

	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/protobuf/proto"
)

// FuzzValidateTaskEnvelope feeds arbitrary bytes through the generated
// decoder and Taskwire's semantic validator. gRPC owns framing and message
// parsing, so the property under test is that validation itself never panics
// and only ever fails with a registered stable error code.
func FuzzValidateTaskEnvelope(f *testing.F) {
	seedEnvelopeCorpus(f)

	f.Fuzz(func(t *testing.T, data []byte) {
		envelope := &pb.TaskEnvelope{}
		if err := proto.Unmarshal(data, envelope); err != nil {
			return // malformed bytes are gRPC's layer, not ours
		}

		err := Validate(envelope)
		if err == nil {
			// An accepted envelope must survive a re-encode round trip.
			reEncoded, marshalErr := proto.Marshal(envelope)
			if marshalErr != nil {
				t.Fatalf("accepted envelope failed to re-encode: %v", marshalErr)
			}
			again := &pb.TaskEnvelope{}
			if err := proto.Unmarshal(reEncoded, again); err != nil {
				t.Fatalf("re-encoded bytes failed to decode: %v", err)
			}
			if err := Validate(again); err != nil {
				t.Fatalf("round-tripped envelope failed validation: %v", err)
			}
			return
		}

		pe, ok := err.(*ProtocolError)
		if !ok {
			t.Fatalf("non-ProtocolError returned: %v", err)
		}
		if _, registered := ErrorRetryable[pe.Code]; !registered {
			t.Fatalf("unregistered error code: %q", pe.Code)
		}
	})
}

// FuzzValidateWorkerRegistration covers the worker handshake, which carries
// the identity and capability constraints the old HELLO message enforced.
func FuzzValidateWorkerRegistration(f *testing.F) {
	seedRegistrationCorpus(f)

	f.Fuzz(func(t *testing.T, data []byte) {
		registration := &pb.WorkerRegistration{}
		if err := proto.Unmarshal(data, registration); err != nil {
			return
		}

		err := Validate(registration)
		if err == nil {
			return
		}
		pe, ok := err.(*ProtocolError)
		if !ok {
			t.Fatalf("non-ProtocolError returned: %v", err)
		}
		if _, registered := ErrorRetryable[pe.Code]; !registered {
			t.Fatalf("unregistered error code: %q", pe.Code)
		}
	})
}

func seedEnvelopeCorpus(f *testing.F) {
	f.Helper()
	valid, _ := proto.Marshal(&pb.TaskEnvelope{
		OwnerId:     make([]byte, 16),
		TaskName:    "t",
		TaskVersion: "1",
		Invocation:  "value",
		Input:       &pb.ValueRef{Location: &pb.ValueRef_Inline{Inline: []byte("x")}, Codec: "msgpack"},
	})
	f.Add(valid)
	empty, _ := proto.Marshal(&pb.TaskEnvelope{})
	f.Add(empty)
	f.Add([]byte{})
	f.Add([]byte{0xff})
}

func seedRegistrationCorpus(f *testing.F) {
	f.Helper()
	valid, _ := proto.Marshal(&pb.WorkerRegistration{
		WorkerId:       "w",
		Runtime:        "python",
		RuntimeVersion: "3.12",
		SdkVersion:     "0.1.0",
		Codecs:         []string{"msgpack"},
		Tasks: &pb.TaskRegistration{
			WorkerId:   "w",
			Generation: 1,
			Tasks:      []*pb.TaskCapability{},
		},
	})
	f.Add(valid)
	empty, _ := proto.Marshal(&pb.WorkerRegistration{})
	f.Add(empty)
	f.Add([]byte{})
}
