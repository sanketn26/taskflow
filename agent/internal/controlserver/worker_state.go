package controlserver

import (
	"bytes"
	"crypto/sha256"

	"github.com/sanketn26/taskwire/agent/pkg/protocol"
	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/protobuf/proto"
)

// workerState is owned by exactly one Work handler. Its lifetime is the
// lifetime of that stream; it is not a transport-independent session model.
type workerState struct {
	WorkerID              string
	Runtime               string
	Codecs                map[string]bool
	CapabilityGeneration  uint64
	CapabilityFingerprint []byte
}

func newWorkerState() *workerState { return &workerState{} }

func (s *workerState) register(registration *pb.WorkerRegistration) error {
	if err := protocol.Validate(registration); err != nil {
		return err
	}
	s.WorkerID = registration.GetWorkerId()
	s.Runtime = registration.GetRuntime()
	s.Codecs = make(map[string]bool, len(registration.GetCodecs()))
	for _, codec := range registration.GetCodecs() {
		s.Codecs[codec] = true
	}
	return s.updateTasks(registration.GetTasks())
}

func (s *workerState) updateTasks(registration *pb.TaskRegistration) error {
	if err := protocol.Validate(registration); err != nil {
		return err
	}
	if registration.GetWorkerId() != s.WorkerID {
		return protocol.NewProtocolError(protocol.OwnerMismatch,
			"worker_id does not match Work stream registration")
	}
	for _, task := range registration.GetTasks() {
		for _, codec := range task.GetCodecs() {
			if !s.Codecs[codec] {
				return protocol.NewProtocolError(protocol.TaskConflict,
					"task codec absent from Work stream registration")
			}
			if s.Runtime != "python" && (task.GetInvocation() == "python_args" || codec == "cloudpickle") {
				return protocol.NewProtocolError(protocol.TaskConflict,
					"runtime cannot provide Python-only capability")
			}
		}
	}
	if registration.GetGeneration() < s.CapabilityGeneration {
		return protocol.NewProtocolError(protocol.TaskConflict, "stale capability generation")
	}
	encoded, err := proto.MarshalOptions{Deterministic: true}.Marshal(registration)
	if err != nil {
		return err
	}
	fingerprint := sha256.Sum256(encoded)
	if registration.GetGeneration() == s.CapabilityGeneration {
		if bytes.Equal(fingerprint[:], s.CapabilityFingerprint) {
			return nil
		}
		return protocol.NewProtocolError(protocol.TaskConflict,
			"conflicting capability generation")
	}
	s.CapabilityGeneration = registration.GetGeneration()
	s.CapabilityFingerprint = append([]byte(nil), fingerprint[:]...)
	return nil
}

func (s *workerState) validatePull(request *pb.PullRequest) error {
	if err := protocol.Validate(request); err != nil {
		return err
	}
	if request.GetWorkerId() != s.WorkerID {
		return protocol.NewProtocolError(protocol.OwnerMismatch,
			"worker_id does not match Work stream registration")
	}
	if request.GetCapabilityGeneration() != s.CapabilityGeneration {
		return protocol.NewProtocolError(protocol.TaskConflict,
			"capability_generation does not match current registration")
	}
	return nil
}
