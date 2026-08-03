package protocol

import (
	"bytes"
	"crypto/sha256"

	"google.golang.org/protobuf/proto"
)

// ConnectionState is the mutable per-stream state owned by the caller (one
// per Work stream). Role authorization and request correlation live in the
// gRPC layer, so this tracks only worker identity and capabilities.
type ConnectionState struct {
	Registered            bool
	WorkerID              string
	Runtime               string
	Codecs                map[string]bool
	CapabilityGeneration  uint64
	CapabilityFingerprint []byte
}

// NewConnectionState returns a fresh, unregistered connection state.
func NewConnectionState() *ConnectionState {
	return &ConnectionState{}
}

// Session is a pure state machine for one worker stream: no sockets. All
// methods take the current ConnectionState explicitly, mutate it in place,
// and return a *ProtocolError the caller turns into a gRPC status.
type Session struct{}

// RegisterWorker records a validated worker registration, including its
// initial capability set.
func (s Session) RegisterWorker(state *ConnectionState, registration *WorkerRegistration) error {
	if err := Validate(registration); err != nil {
		return err
	}
	state.Registered = true
	state.WorkerID = registration.WorkerId
	state.Runtime = registration.Runtime
	state.Codecs = make(map[string]bool, len(registration.Codecs))
	for _, codec := range registration.Codecs {
		state.Codecs[codec] = true
	}
	state.CapabilityGeneration = 0
	state.CapabilityFingerprint = nil
	return s.RegisterTasks(state, registration.Tasks)
}

// RegisterTasks atomically replaces the stream's capability set. A repeated
// generation with identical content is idempotent; an older or conflicting
// generation is task_conflict.
func (Session) RegisterTasks(state *ConnectionState, registration *TaskRegistration) error {
	if !state.Registered {
		return NewProtocolError(NotRegistered, "worker has not registered")
	}
	if registration == nil {
		return NewProtocolError(InvalidMessage, "task registration is required")
	}
	if err := Validate(registration); err != nil {
		return err
	}
	if registration.WorkerId != state.WorkerID {
		return NewProtocolError(OwnerMismatch, "worker_id does not match registration")
	}
	for _, task := range registration.Tasks {
		for _, codec := range task.Codecs {
			if !state.Codecs[codec] {
				return NewProtocolError(TaskConflict, "task codec absent from worker registration")
			}
			if state.Runtime != "python" && (task.Invocation == "python_args" || codec == "cloudpickle") {
				return NewProtocolError(TaskConflict, "runtime cannot provide Python-only capability")
			}
		}
	}
	if registration.Generation < state.CapabilityGeneration {
		return NewProtocolError(TaskConflict, "stale capability generation")
	}
	encoded, err := proto.MarshalOptions{Deterministic: true}.Marshal(registration)
	if err != nil {
		return err
	}
	fingerprint := sha256.Sum256(encoded)
	if registration.Generation == state.CapabilityGeneration {
		if bytes.Equal(fingerprint[:], state.CapabilityFingerprint) {
			return nil
		}
		return NewProtocolError(TaskConflict, "conflicting capability generation")
	}
	state.CapabilityGeneration = registration.Generation
	state.CapabilityFingerprint = append([]byte(nil), fingerprint[:]...)
	return nil
}

// CheckOwner verifies ownerID matches the caller's authenticated owner.
func (Session) CheckOwner(caller CallerIdentity, ownerID []byte) error {
	if !bytes.Equal(caller.OwnerID, ownerID) {
		return NewProtocolError(OwnerMismatch, "owner_id does not match authenticated owner")
	}
	return nil
}

// OwnerRegistry tracks which WatchResults stream is primary for each owner
// ID. A newer stream for the same owner becomes primary once it opens; the
// old stream stops receiving new notifications.
type OwnerRegistry struct {
	primary map[string]interface{}
}

func NewOwnerRegistry() *OwnerRegistry {
	return &OwnerRegistry{primary: make(map[string]interface{})}
}

// Promote makes streamKey primary for ownerID and returns the previous
// primary stream key, or nil if there wasn't one.
func (r *OwnerRegistry) Promote(ownerID []byte, streamKey interface{}) interface{} {
	key := string(ownerID)
	previous := r.primary[key]
	r.primary[key] = streamKey
	return previous
}

func (r *OwnerRegistry) IsPrimary(ownerID []byte, streamKey interface{}) bool {
	return r.primary[string(ownerID)] == streamKey
}

func (r *OwnerRegistry) Remove(ownerID []byte, streamKey interface{}) {
	key := string(ownerID)
	if r.primary[key] == streamKey {
		delete(r.primary, key)
	}
}
