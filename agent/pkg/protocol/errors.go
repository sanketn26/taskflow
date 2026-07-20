// Package protocol implements the wire frame, msgpack envelope, and pure
// session state machine shared with the Python SDK.
package protocol

// Stable v1 error codes. Values are wire-visible contract, not free-form
// Go strings: both languages expose the same code list and retryability
// lookup.
const (
	UnsupportedVersion = "unsupported_version"
	UnknownMessageType = "unknown_message_type"
	UnknownFlags       = "unknown_flags"
	FrameTooLargeCode  = "frame_too_large"
	MalformedPayload   = "malformed_payload"
	InvalidMessage     = "invalid_message"
	DuplicateRequest   = "duplicate_request"
	NotRegistered      = "not_registered"
	RoleForbidden      = "role_forbidden"
	OwnerMismatch      = "owner_mismatch"
	TaskConflict       = "task_conflict"
	TaskNotFound       = "task_not_found"
	TooLate            = "too_late"
	StaleLease         = "stale_lease"
	UnknownLease       = "unknown_lease"
	UnknownTask        = "unknown_task"
	UnsupportedCodec   = "unsupported_codec"
	UnsupportedBackend = "unsupported_backend"
	TransferLimit      = "transfer_limit"
	TransferTimeout    = "transfer_timeout"
	ChecksumMismatch   = "checksum_mismatch"
	StorageUnavailable = "storage_unavailable"
	StorageConsistency = "storage_consistency"
	Shutdown           = "shutdown"
	Internal           = "internal"
)

// System-generated Failure.code values (separate namespace from the Error
// registry above).
const (
	FailureTaskException       = "task_exception"
	FailureUnknownTask         = "unknown_task"
	FailureSerializationError  = "serialization_error"
	FailureMaxAttemptsExceeded = "max_attempts_exceeded"
	FailureStorageConsistency  = "storage_consistency"
	FailureCancelled           = "cancelled"
)

// ErrorRetryable is the stable retryability lookup for every Error code.
var ErrorRetryable = map[string]bool{
	UnsupportedVersion: false,
	UnknownMessageType: false,
	UnknownFlags:       false,
	FrameTooLargeCode:  false,
	MalformedPayload:   false,
	InvalidMessage:     false,
	DuplicateRequest:   false,
	NotRegistered:      false,
	RoleForbidden:      false,
	OwnerMismatch:      false,
	TaskConflict:       false,
	TaskNotFound:       false,
	TooLate:            false,
	StaleLease:         false,
	UnknownLease:       false,
	UnknownTask:        false,
	UnsupportedCodec:   false,
	UnsupportedBackend: false,
	TransferLimit:      true,
	TransferTimeout:    true,
	ChecksumMismatch:   false,
	StorageUnavailable: true,
	StorageConsistency: false,
	Shutdown:           true,
	Internal:           true,
}

// DecodeError is returned by every decode path in this package. Message
// may contain offsets/field names but never payload values.
type DecodeError struct {
	Code    string
	Message string
}

func (e *DecodeError) Error() string {
	return e.Code + ": " + e.Message
}

// NewDecodeError constructs a DecodeError, panicking if code is not a
// registered stable error code (a programmer error, not a runtime one).
func NewDecodeError(code, message string) *DecodeError {
	if _, ok := ErrorRetryable[code]; !ok {
		panic("protocol: unregistered error code: " + code)
	}
	return &DecodeError{Code: code, Message: message}
}

// TruncatedFrame is returned when EOF occurs after at least one
// header/payload byte was read.
func TruncatedFrame(message string) *DecodeError {
	return NewDecodeError(MalformedPayload, message)
}

// FrameTooLarge is returned when a claimed payload length exceeds the
// configured maximum.
func FrameTooLarge(message string) *DecodeError {
	return NewDecodeError(FrameTooLargeCode, message)
}
