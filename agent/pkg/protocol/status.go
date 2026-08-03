package protocol

import (
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
)

// grpcCode maps each stable Taskwire error code onto the gRPC status code
// that carries the same meaning to a generic client. The Taskwire code
// remains authoritative and travels in the status details, so a client that
// understands Taskwire never has to infer meaning from the transport code.
var grpcCode = map[string]codes.Code{
	MalformedPayload:   codes.InvalidArgument,
	InvalidMessage:     codes.InvalidArgument,
	NotRegistered:      codes.FailedPrecondition,
	RoleForbidden:      codes.PermissionDenied,
	OwnerMismatch:      codes.PermissionDenied,
	TaskConflict:       codes.Aborted,
	TaskNotFound:       codes.NotFound,
	TooLate:            codes.FailedPrecondition,
	StaleLease:         codes.Aborted,
	UnknownLease:       codes.NotFound,
	UnknownTask:        codes.NotFound,
	UnsupportedCodec:   codes.InvalidArgument,
	UnsupportedBackend: codes.InvalidArgument,
	TransferLimit:      codes.ResourceExhausted,
	TransferTimeout:    codes.DeadlineExceeded,
	ChecksumMismatch:   codes.DataLoss,
	StorageUnavailable: codes.Unavailable,
	StorageConsistency: codes.DataLoss,
	Shutdown:           codes.Unavailable,
	Internal:           codes.Internal,
}

// GRPCCode returns the gRPC status code for a stable Taskwire error code.
func GRPCCode(code string) codes.Code {
	if c, ok := grpcCode[code]; ok {
		return c
	}
	return codes.Unknown
}

// StatusError builds a gRPC status error carrying the Taskwire error code,
// message, and retryability in its details.
func StatusError(code, message string) error {
	st := status.New(GRPCCode(code), message)
	detailed, err := st.WithDetails(&pb.Error{
		Code:      code,
		Message:   message,
		Retryable: ErrorRetryable[code],
		Details:   map[string]string{},
	})
	if err != nil {
		return st.Err()
	}
	return detailed.Err()
}

// StatusFromError converts a *ProtocolError into a gRPC status error,
// passing through anything that is already a status error.
func StatusFromError(err error) error {
	if err == nil {
		return nil
	}
	if pe, ok := err.(*ProtocolError); ok {
		return StatusError(pe.Code, pe.Message)
	}
	if _, ok := status.FromError(err); ok {
		return err
	}
	return StatusError(Internal, err.Error())
}

// ErrorFromStatus extracts the Taskwire Error detail from a gRPC status
// error. It returns nil when err carries no Taskwire detail.
func ErrorFromStatus(err error) *pb.Error {
	st, ok := status.FromError(err)
	if !ok {
		return nil
	}
	for _, detail := range st.Details() {
		if e, ok := detail.(*pb.Error); ok {
			return e
		}
	}
	return nil
}
