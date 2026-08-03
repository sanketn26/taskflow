// Package protocol implements the gRPC control-plane contract: stable error
// codes and their gRPC status mapping, role authorization, semantic message
// validation, and the pure session state machine shared with the Python SDK.
package protocol

// Stable v1 error codes. Values are wire-visible contract, not free-form
// Go strings: both languages expose the same code list and retryability
// lookup. Transport-level failures (unsupported version, unknown method,
// oversize message) are gRPC's responsibility and no longer appear here.
const (
	MalformedPayload   = "malformed_payload"
	InvalidMessage     = "invalid_message"
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
	MalformedPayload:   false,
	InvalidMessage:     false,
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

// ProtocolError carries a stable Taskwire error code. Callers convert it to
// a gRPC status with StatusFromError. Message may contain field names but
// never payload values.
type ProtocolError struct {
	Code    string
	Message string
}

func (e *ProtocolError) Error() string {
	return e.Code + ": " + e.Message
}

// NewProtocolError constructs a ProtocolError, panicking if code is not a
// registered stable error code (a programmer error, not a runtime one).
func NewProtocolError(code, message string) *ProtocolError {
	if _, ok := ErrorRetryable[code]; !ok {
		panic("protocol: unregistered error code: " + code)
	}
	return &ProtocolError{Code: code, Message: message}
}
