package protocol

import (
	"bytes"
	"testing"

	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"
)

func TestProtobufPayloadRoundTrip(t *testing.T) {
	original := &Hello{Role: "worker", WorkerId: "worker-1", Runtime: "go", RuntimeVersion: "1.26", SdkVersion: "0.1.0", Codecs: []string{"msgpack", "bytes"}}
	payload, err := EncodePayload(MessageHello, original)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := DecodePayload(MessageHello, payload)
	if err != nil {
		t.Fatal(err)
	}
	if !proto.Equal(decoded, original) {
		t.Fatalf("round trip differs: %v", decoded)
	}
}

func TestProtobufBodyMustMatchFrameType(t *testing.T) {
	payload, err := EncodePayload(MessageHello, &Hello{Role: "admin"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = DecodePayload(MessageStatus, payload)
	if err == nil || err.(*DecodeError).Code != InvalidMessage {
		t.Fatalf("expected invalid_message, got %v", err)
	}
}

func TestProtobufUnknownFieldsAreForwardCompatible(t *testing.T) {
	unknown := protowire.AppendVarint(protowire.AppendTag(nil, 100, protowire.VarintType), 42)
	helloBytes, err := proto.Marshal(&Hello{Role: "admin"})
	if err != nil {
		t.Fatal(err)
	}
	hello := &Hello{}
	if err := proto.Unmarshal(append(helloBytes, unknown...), hello); err != nil {
		t.Fatal(err)
	}
	payload, err := EncodePayload(MessageHello, hello)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := DecodePayload(MessageHello, payload)
	if err != nil {
		t.Fatal(err)
	}
	reencoded, err := EncodePayload(MessageHello, decoded)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(reencoded, unknown) {
		t.Fatal("unknown protobuf field was not preserved")
	}
}

func TestMalformedProtobufRejected(t *testing.T) {
	_, err := DecodePayload(MessageHello, []byte{0x0a, 0xff})
	if err == nil || err.(*DecodeError).Code != MalformedPayload {
		t.Fatalf("expected malformed_payload, got %v", err)
	}
}

func TestRequiredSemanticValidation(t *testing.T) {
	_, err := EncodePayload(MessageHello, &Hello{Role: "runtime", OwnerId: []byte("short")})
	if err == nil || err.(*DecodeError).Code != InvalidMessage {
		t.Fatalf("expected invalid_message, got %v", err)
	}

	value := &ValueRef{Location: &pb.ValueRef_Inline{Inline: []byte("value")}}
	if err := validateMessage(value); err == nil {
		t.Fatal("inline ValueRef without codec accepted")
	}
}

func TestControlPlaneUsesProtobufEnvelope(t *testing.T) {
	payload, err := EncodePayload(MessageStatus, &StatusRequest{})
	if err != nil {
		t.Fatal(err)
	}
	envelope := &pb.ControlMessage{}
	if err := proto.Unmarshal(payload, envelope); err != nil {
		t.Fatal(err)
	}
	if envelope.GetStatusRequest() == nil {
		t.Fatal("missing status_request oneof branch")
	}
}

func TestNewAckPopulatesAndValidatesTypedFields(t *testing.T) {
	taskID := bytes.Repeat([]byte{1}, 16)
	ack, err := NewAck("submit", map[string]interface{}{"task_id": taskID})
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(ack.TaskId, taskID) {
		t.Fatal("task_id was dropped")
	}
	if _, err := NewAck("submit", nil); err == nil {
		t.Fatal("incomplete ACK accepted")
	}
	if _, err := NewAck("unknown", nil); err == nil {
		t.Fatal("unknown ACK kind accepted")
	}
}

func TestSemanticValidationRecursesIntoNestedMessages(t *testing.T) {
	ownerID := bytes.Repeat([]byte{1}, 16)
	bad := &TaskEnvelope{OwnerId: ownerID, TaskName: "task", TaskVersion: "1", Invocation: "value", Input: &ValueRef{Location: &pb.ValueRef_Object{Object: &ObjectRef{Store: "s", Key: "k", Codec: "bytes", Sha256: []byte("short")}}}}
	if _, err := EncodePayload(MessageSubmit, bad); err == nil {
		t.Fatal("invalid nested ObjectRef accepted")
	}
	completion := &Completion{LeaseId: nil, Outcome: &pb.Completion_Result{Result: &ObjectRef{Store: "s", Key: "k", Codec: "bytes", Sha256: bytes.Repeat([]byte{0}, 32)}}}
	if _, err := EncodePayload(MessageComplete, completion); err == nil {
		t.Fatal("missing lease_id accepted")
	}
}

func TestHelloRoleFieldsAreExclusive(t *testing.T) {
	hello := &Hello{Role: "runtime", OwnerId: bytes.Repeat([]byte{1}, 16), WorkerId: "unexpected"}
	if _, err := EncodePayload(MessageHello, hello); err == nil {
		t.Fatal("runtime HELLO with worker fields accepted")
	}
	worker := &Hello{Role: "worker", WorkerId: "w", Runtime: "go", RuntimeVersion: "1", SdkVersion: "1", Codecs: []string{"msgpack", "msgpack"}}
	if _, err := EncodePayload(MessageHello, worker); err == nil {
		t.Fatal("duplicate worker codecs accepted")
	}
}

func TestEncodeTypeMismatchReturnsRegisteredError(t *testing.T) {
	_, err := EncodePayload(MessageHeartbeat, &PullRequest{WorkerId: "w", CapabilityGeneration: 1})
	decodeErr, ok := err.(*DecodeError)
	if !ok || decodeErr.Code != InvalidMessage {
		t.Fatalf("expected invalid_message DecodeError, got %T %v", err, err)
	}
}
