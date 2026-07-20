package protocol

import (
	"encoding/binary"
	"errors"
	"io"
)

const (
	HeaderSize      = 31
	ProtocolVersion = 1
	MaxUint32       = 0xFFFFFFFF
)

// Flags is a bit field: 0x01 error, 0x02 idempotent, 0x04 forwarded.
type Flags uint8

const (
	FlagNone       Flags = 0x00
	FlagError      Flags = 0x01
	FlagIdempotent Flags = 0x02
	FlagForwarded  Flags = 0x04

	allFlags = FlagError | FlagIdempotent | FlagForwarded
)

// MessageType identifies the payload schema carried by a frame.
type MessageType uint8

const (
	MessageSubmit        MessageType = 0x01
	MessagePull          MessageType = 0x02
	MessageTask          MessageType = 0x03
	MessageHeartbeat     MessageType = 0x04
	MessageResult        MessageType = 0x05
	MessageCancel        MessageType = 0x06
	MessageComplete      MessageType = 0x07
	MessageSteal         MessageType = 0x08
	MessageAck           MessageType = 0x09
	MessageStatus        MessageType = 0x0A
	MessageResumeResults MessageType = 0x0B
	MessageError         MessageType = 0x0C
	MessageObjectPut     MessageType = 0x0D
	MessageObjectGet     MessageType = 0x0E
	MessageObjectChunk   MessageType = 0x0F
	MessageHello         MessageType = 0x10
	MessageTaskQuery     MessageType = 0x11
	MessageRegisterTasks MessageType = 0x12
)

func (m MessageType) valid() bool {
	return m >= MessageSubmit && m <= MessageRegisterTasks
}

func (m MessageType) String() string {
	names := map[MessageType]string{
		MessageSubmit: "SUBMIT", MessagePull: "PULL", MessageTask: "TASK",
		MessageHeartbeat: "HEARTBEAT", MessageResult: "RESULT", MessageCancel: "CANCEL",
		MessageComplete: "COMPLETE", MessageSteal: "STEAL", MessageAck: "ACK",
		MessageStatus: "STATUS", MessageResumeResults: "RESUME_RESULTS", MessageError: "ERROR",
		MessageObjectPut: "OBJECT_PUT", MessageObjectGet: "OBJECT_GET", MessageObjectChunk: "OBJECT_CHUNK",
		MessageHello: "HELLO", MessageTaskQuery: "TASK_QUERY", MessageRegisterTasks: "REGISTER_TASKS",
	}
	if n, ok := names[m]; ok {
		return n
	}
	return "UNKNOWN"
}

// idempotentAllowed / forwardedAllowed mirror the Python frame layer's
// per-flag message-type allow-lists.
var idempotentAllowed = map[MessageType]bool{
	MessageSubmit: true, MessageComplete: true, MessageAck: true, MessageObjectPut: true,
}

var forwardedAllowed = map[MessageType]bool{
	MessageSubmit: true, MessageComplete: true,
}

// FrameHeader is the decoded 31-byte header.
type FrameHeader struct {
	Version     uint8
	MessageType MessageType
	TaskID      [16]byte
	RequestID   uint64
	Flags       Flags
	PayloadLen  uint32
}

// Frame is a decoded frame: header fields plus payload bytes.
type Frame struct {
	Version     uint8
	MessageType MessageType
	TaskID      [16]byte
	RequestID   uint64
	Flags       Flags
	Payload     []byte
}

func validateFlagType(mt MessageType, flags Flags) error {
	isError := flags&FlagError != 0
	if isError != (mt == MessageError) {
		return NewDecodeError(UnknownFlags, "error flag is required exactly on ERROR frames")
	}
	if flags&FlagIdempotent != 0 && !idempotentAllowed[mt] {
		return NewDecodeError(UnknownFlags, "idempotent flag invalid on message type "+mt.String())
	}
	if flags&FlagForwarded != 0 && !forwardedAllowed[mt] {
		return NewDecodeError(UnknownFlags, "forwarded flag invalid on message type "+mt.String())
	}
	return nil
}

// EncodeFrame renders frame as its 31-byte header followed by the payload.
func EncodeFrame(frame Frame, maxPayloadBytes uint32) ([]byte, error) {
	if uint32(len(frame.Payload)) > maxPayloadBytes {
		return nil, FrameTooLarge("payload length exceeds configured max")
	}
	if !frame.MessageType.valid() {
		return nil, NewDecodeError(UnknownMessageType, "unknown message type")
	}
	if err := validateFlagType(frame.MessageType, frame.Flags); err != nil {
		return nil, err
	}

	out := make([]byte, HeaderSize+len(frame.Payload))
	out[0] = frame.Version
	out[1] = uint8(frame.MessageType)
	copy(out[2:18], frame.TaskID[:])
	binary.BigEndian.PutUint64(out[18:26], frame.RequestID)
	out[26] = uint8(frame.Flags)
	binary.BigEndian.PutUint32(out[27:31], uint32(len(frame.Payload)))
	copy(out[HeaderSize:], frame.Payload)
	return out, nil
}

// DecodeHeader validates and decodes exactly HeaderSize bytes.
func DecodeHeader(data []byte, maxPayloadBytes uint32) (FrameHeader, error) {
	if len(data) != HeaderSize {
		panic("protocol: DecodeHeader requires exactly HeaderSize bytes")
	}

	var h FrameHeader
	h.Version = data[0]
	if h.Version != ProtocolVersion {
		return FrameHeader{}, NewDecodeError(UnsupportedVersion, "unsupported protocol version")
	}

	mt := MessageType(data[1])
	if !mt.valid() {
		return FrameHeader{}, NewDecodeError(UnknownMessageType, "unknown message type")
	}
	h.MessageType = mt

	copy(h.TaskID[:], data[2:18])
	h.RequestID = binary.BigEndian.Uint64(data[18:26])

	flagsRaw := data[26]
	if Flags(flagsRaw)&^allFlags != 0 {
		return FrameHeader{}, NewDecodeError(UnknownFlags, "unknown flag bits")
	}
	h.Flags = Flags(flagsRaw)

	if err := validateFlagType(h.MessageType, h.Flags); err != nil {
		return FrameHeader{}, err
	}

	h.PayloadLen = binary.BigEndian.Uint32(data[27:31])
	if h.PayloadLen > maxPayloadBytes {
		return FrameHeader{}, FrameTooLarge("payload length exceeds configured max")
	}

	return h, nil
}

// ReadFrame reads one frame from r. It returns (nil, nil) only when EOF
// occurs before any header byte is read; any EOF after that point returns
// a TruncatedFrame error. The header is fully validated — including the
// payload-length bound — before a payload buffer is allocated.
func ReadFrame(r io.Reader, maxPayloadBytes uint32) (*Frame, error) {
	var header [HeaderSize]byte
	n, err := io.ReadFull(r, header[:])
	if n == 0 && errors.Is(err, io.EOF) {
		return nil, nil
	}
	if err != nil {
		return nil, TruncatedFrame("EOF within frame header")
	}

	h, err := DecodeHeader(header[:], maxPayloadBytes)
	if err != nil {
		return nil, err
	}

	payload := make([]byte, h.PayloadLen)
	if h.PayloadLen > 0 {
		if _, err := io.ReadFull(r, payload); err != nil {
			return nil, TruncatedFrame("EOF within frame payload")
		}
	}

	return &Frame{
		Version:     h.Version,
		MessageType: h.MessageType,
		TaskID:      h.TaskID,
		RequestID:   h.RequestID,
		Flags:       h.Flags,
		Payload:     payload,
	}, nil
}

// WriteFrame encodes and writes frame to w, handling short writes.
func WriteFrame(w io.Writer, frame Frame, maxPayloadBytes uint32) error {
	data, err := EncodeFrame(frame, maxPayloadBytes)
	if err != nil {
		return err
	}
	total := 0
	for total < len(data) {
		n, err := w.Write(data[total:])
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		total += n
	}
	return nil
}
