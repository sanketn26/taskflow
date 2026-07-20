package protocol

import (
	"bytes"
	"crypto/sha256"
)

// ConnectionState is the mutable per-connection state owned by the
// caller (one per socket).
type ConnectionState struct {
	Registered            bool
	Role                  string
	OwnerID               []byte
	WorkerID              string
	Runtime               string
	Codecs                map[string]bool
	CapabilityGeneration  uint64
	CapabilityFingerprint []byte
	InFlightRequestIDs    map[uint64]bool
}

// NewConnectionState returns a fresh, unregistered connection state.
func NewConnectionState() *ConnectionState {
	return &ConnectionState{InFlightRequestIDs: make(map[uint64]bool)}
}

// ResetRequestNamespace clears in-flight request IDs on reconnect.
func (s *ConnectionState) ResetRequestNamespace() {
	s.InFlightRequestIDs = make(map[uint64]bool)
}

var runtimeMessages = map[MessageType]bool{
	MessageSubmit: true, MessageCancel: true, MessageResumeResults: true,
	MessageTaskQuery: true, MessageAck: true,
}

var workerMessages = map[MessageType]bool{
	MessageRegisterTasks: true, MessagePull: true, MessageHeartbeat: true, MessageComplete: true,
}

var adminMessages = map[MessageType]bool{
	MessageStatus: true,
}

func roleMessages(role string) map[MessageType]bool {
	switch role {
	case "runtime":
		return runtimeMessages
	case "worker":
		return workerMessages
	case "admin":
		return adminMessages
	default:
		return nil
	}
}

// Session is a pure state machine for one connection: no sockets. All
// methods take the current ConnectionState explicitly, mutate it in
// place, and return a *DecodeError the caller turns into an ERROR frame.
type Session struct{}

// Register records a successful HELLO.
func (Session) Register(state *ConnectionState, role string, ownerID []byte, workerID string) {
	state.Registered = true
	state.Role = role
	state.OwnerID = ownerID
	state.WorkerID = workerID
}

func (Session) RegisterWorker(state *ConnectionState, hello *Hello) {
	state.Registered = true
	state.Role = "worker"
	state.WorkerID = hello.WorkerID
	state.Runtime = hello.Runtime
	state.Codecs = make(map[string]bool, len(hello.Codecs))
	for _, codec := range hello.Codecs {
		state.Codecs[codec] = true
	}
	state.CapabilityGeneration = 0
	state.CapabilityFingerprint = nil
}

// Authorize checks that messageType is permitted for state's role, and
// that HELLO has already been sent for anything but HELLO itself.
func (Session) Authorize(state *ConnectionState, messageType MessageType) error {
	if messageType == MessageHello {
		return nil
	}
	if !state.Registered {
		return NewDecodeError(NotRegistered, "connection has not sent HELLO")
	}
	allowed := roleMessages(state.Role)
	if !allowed[messageType] {
		return NewDecodeError(RoleForbidden, "role "+state.Role+" may not send "+messageType.String())
	}
	if state.Role == "worker" && messageType == MessagePull && state.CapabilityGeneration == 0 {
		return NewDecodeError(NotRegistered, "worker has not registered task capabilities")
	}
	return nil
}

func (Session) RegisterTasks(state *ConnectionState, registration *TaskRegistration) error {
	if state.Role != "worker" {
		return NewDecodeError(RoleForbidden, "only workers register tasks")
	}
	if registration.WorkerID != state.WorkerID {
		return NewDecodeError(OwnerMismatch, "worker_id does not match HELLO")
	}
	for _, task := range registration.Tasks {
		for _, codec := range task.Codecs {
			if !state.Codecs[codec] {
				return NewDecodeError(TaskConflict, "task codec absent from HELLO")
			}
			if state.Runtime != "python" && (task.Invocation == "python_args" || codec == "cloudpickle") {
				return NewDecodeError(TaskConflict, "runtime cannot provide Python-only capability")
			}
		}
	}
	if registration.Generation < state.CapabilityGeneration {
		return NewDecodeError(TaskConflict, "stale capability generation")
	}
	encoded, err := registration.Encode()
	if err != nil {
		return err
	}
	fingerprint := sha256.Sum256(encoded)
	if registration.Generation == state.CapabilityGeneration {
		if bytes.Equal(fingerprint[:], state.CapabilityFingerprint) {
			return nil
		}
		return NewDecodeError(TaskConflict, "conflicting capability generation")
	}
	state.CapabilityGeneration = registration.Generation
	state.CapabilityFingerprint = append([]byte(nil), fingerprint[:]...)
	return nil
}

// BeginRequest registers a nonzero request ID as in flight; rejects reuse.
func (Session) BeginRequest(state *ConnectionState, requestID uint64) error {
	if requestID == 0 {
		return nil
	}
	if state.InFlightRequestIDs[requestID] {
		return NewDecodeError(DuplicateRequest, "request id already in flight")
	}
	state.InFlightRequestIDs[requestID] = true
	return nil
}

// CompleteRequest frees a request ID once its response has been sent.
func (Session) CompleteRequest(state *ConnectionState, requestID uint64) {
	delete(state.InFlightRequestIDs, requestID)
}

// CheckOwner verifies ownerID matches the connection's registered owner.
func (Session) CheckOwner(state *ConnectionState, ownerID []byte) error {
	if string(state.OwnerID) != string(ownerID) {
		return NewDecodeError(OwnerMismatch, "owner_id does not match registered owner")
	}
	return nil
}

// OwnerRegistry tracks which connection is primary for each owner ID. A
// newer connection for the same owner becomes primary once its resume
// begins; the caller detects "resume begins" and calls Promote. The old
// connection may finish in-flight responses but stops receiving new
// notifications once replaced.
type OwnerRegistry struct {
	primary map[string]interface{}
}

func NewOwnerRegistry() *OwnerRegistry {
	return &OwnerRegistry{primary: make(map[string]interface{})}
}

// Promote makes connectionKey primary for ownerID and returns the
// previous primary connection key, or nil if there wasn't one.
func (r *OwnerRegistry) Promote(ownerID []byte, connectionKey interface{}) interface{} {
	key := string(ownerID)
	previous := r.primary[key]
	r.primary[key] = connectionKey
	return previous
}

func (r *OwnerRegistry) IsPrimary(ownerID []byte, connectionKey interface{}) bool {
	return r.primary[string(ownerID)] == connectionKey
}

func (r *OwnerRegistry) Remove(ownerID []byte, connectionKey interface{}) {
	key := string(ownerID)
	if r.primary[key] == connectionKey {
		delete(r.primary, key)
	}
}
