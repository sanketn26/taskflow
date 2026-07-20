package protocol

import (
	"bytes"
	"io"
	"testing"
)

const maxPayload = 16 * 1024 * 1024

var zeroID [16]byte

func TestHeaderIsExactly31Bytes(t *testing.T) {
	frame := Frame{Version: 1, MessageType: MessagePull, TaskID: zeroID, RequestID: 1, Flags: FlagNone, Payload: nil}
	encoded, err := EncodeFrame(frame, maxPayload)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) != HeaderSize {
		t.Fatalf("got %d bytes, want %d", len(encoded), HeaderSize)
	}
}

func TestRoundTripWithPayload(t *testing.T) {
	frame := Frame{Version: 1, MessageType: MessageStatus, TaskID: zeroID, RequestID: 42, Flags: FlagNone, Payload: []byte{0x80}}
	encoded, err := EncodeFrame(frame, maxPayload)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := ReadFrame(bytes.NewReader(encoded), maxPayload)
	if err != nil {
		t.Fatal(err)
	}
	if decoded.MessageType != frame.MessageType || decoded.RequestID != frame.RequestID || !bytes.Equal(decoded.Payload, frame.Payload) {
		t.Fatalf("round trip mismatch: %+v vs %+v", decoded, frame)
	}
}

func TestValidFlagTypeCombinations(t *testing.T) {
	cases := []struct {
		mt    MessageType
		flags Flags
	}{
		{MessageSubmit, FlagIdempotent},
		{MessageSubmit, FlagForwarded},
		{MessageComplete, FlagIdempotent},
		{MessageComplete, FlagForwarded},
		{MessageAck, FlagIdempotent},
		{MessageObjectPut, FlagIdempotent},
		{MessageError, FlagError},
	}
	for _, c := range cases {
		frame := Frame{Version: 1, MessageType: c.mt, TaskID: zeroID, RequestID: 1, Flags: c.flags}
		if _, err := EncodeFrame(frame, maxPayload); err != nil {
			t.Errorf("mt=%v flags=%v: unexpected error %v", c.mt, c.flags, err)
		}
	}
}

func TestInvalidFlagTypeCombinationsRejected(t *testing.T) {
	cases := []struct {
		mt    MessageType
		flags Flags
	}{
		{MessagePull, FlagIdempotent},
		{MessagePull, FlagForwarded},
		{MessageHeartbeat, FlagForwarded},
		{MessageStatus, FlagError},
		{MessageError, FlagNone},
		{MessageSteal, FlagForwarded},
	}
	for _, c := range cases {
		frame := Frame{Version: 1, MessageType: c.mt, TaskID: zeroID, RequestID: 1, Flags: c.flags}
		_, err := EncodeFrame(frame, maxPayload)
		de, ok := err.(*DecodeError)
		if !ok || de.Code != UnknownFlags {
			t.Errorf("mt=%v flags=%v: got %v, want unknown_flags", c.mt, c.flags, err)
		}
	}
}

func header(version, messageType byte, requestID uint64, flags byte, payloadLen uint32) []byte {
	h := make([]byte, HeaderSize)
	h[0] = version
	h[1] = messageType
	for i := 0; i < 8; i++ {
		h[18+i] = byte(requestID >> uint(8*(7-i)))
	}
	h[26] = flags
	for i := 0; i < 4; i++ {
		h[27+i] = byte(payloadLen >> uint(8*(3-i)))
	}
	return h
}

func TestUnsupportedVersionRejected(t *testing.T) {
	h := header(2, byte(MessagePull), 0, 0, 0)
	_, err := DecodeHeader(h, maxPayload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != UnsupportedVersion {
		t.Fatalf("got %v, want unsupported_version", err)
	}
}

func TestUnknownMessageTypeRejected(t *testing.T) {
	h := header(1, 0xFE, 0, 0, 0)
	_, err := DecodeHeader(h, maxPayload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != UnknownMessageType {
		t.Fatalf("got %v, want unknown_message_type", err)
	}
}

func TestUnknownFlagBitsRejected(t *testing.T) {
	h := header(1, byte(MessagePull), 0, 0x08, 0)
	_, err := DecodeHeader(h, maxPayload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != UnknownFlags {
		t.Fatalf("got %v, want unknown_flags", err)
	}
}

func TestFrameTooLargeCheckedBeforePayloadAllocation(t *testing.T) {
	h := header(1, byte(MessagePull), 0, 0, 0xFFFFFFFF)
	_, err := DecodeHeader(h, maxPayload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != FrameTooLargeCode {
		t.Fatalf("got %v, want frame_too_large", err)
	}

	var reads []int
	r := &countingReader{data: h, onRead: func(n int) { reads = append(reads, n) }}
	_, err = ReadFrame(r, maxPayload)
	de, ok = err.(*DecodeError)
	if !ok || de.Code != FrameTooLargeCode {
		t.Fatalf("got %v, want frame_too_large", err)
	}
	if len(reads) != 1 || reads[0] != HeaderSize {
		t.Fatalf("expected exactly one read of HeaderSize bytes, got %v", reads)
	}
}

type countingReader struct {
	data   []byte
	pos    int
	onRead func(n int)
}

func (r *countingReader) Read(p []byte) (int, error) {
	n := copy(p, r.data[r.pos:])
	r.pos += n
	r.onRead(n)
	if n == 0 {
		return 0, io.EOF
	}
	return n, nil
}

func TestCleanEOFBeforeAnyByteReturnsNil(t *testing.T) {
	frame, err := ReadFrame(bytes.NewReader(nil), maxPayload)
	if err != nil || frame != nil {
		t.Fatalf("got (%v, %v), want (nil, nil)", frame, err)
	}
}

func TestTruncatedHeaderRaises(t *testing.T) {
	f := Frame{Version: 1, MessageType: MessagePull, TaskID: zeroID, RequestID: 1}
	encoded, _ := EncodeFrame(f, maxPayload)
	for _, cutoff := range []int{1, 5, 15, 30} {
		_, err := ReadFrame(bytes.NewReader(encoded[:cutoff]), maxPayload)
		de, ok := err.(*DecodeError)
		if !ok || de.Code != MalformedPayload {
			t.Errorf("cutoff=%d: got %v, want malformed_payload", cutoff, err)
		}
	}
}

func TestTruncatedPayloadRaises(t *testing.T) {
	f := Frame{Version: 1, MessageType: MessageStatus, TaskID: zeroID, RequestID: 1, Payload: []byte{0x80}}
	encoded, _ := EncodeFrame(f, maxPayload)
	_, err := ReadFrame(bytes.NewReader(encoded[:len(encoded)-1]), maxPayload)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != MalformedPayload {
		t.Fatalf("got %v, want malformed_payload", err)
	}
}

func TestEncodeFrameRejectsOversizePayload(t *testing.T) {
	f := Frame{Version: 1, MessageType: MessageStatus, TaskID: zeroID, RequestID: 1, Payload: make([]byte, 10)}
	_, err := EncodeFrame(f, 5)
	de, ok := err.(*DecodeError)
	if !ok || de.Code != FrameTooLargeCode {
		t.Fatalf("got %v, want frame_too_large", err)
	}
}
