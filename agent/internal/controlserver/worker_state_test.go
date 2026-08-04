package controlserver

import (
	"testing"

	"github.com/sanketn26/taskwire/agent/pkg/protocol"
	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
)

func registration(workerID, runtime string, generation uint64, tasks ...*pb.TaskCapability) *pb.WorkerRegistration {
	return &pb.WorkerRegistration{
		WorkerId: workerID, Runtime: runtime, RuntimeVersion: "1", SdkVersion: "0.1.0",
		Codecs: []string{"msgpack"},
		Tasks:  &pb.TaskRegistration{WorkerId: workerID, Generation: generation, Tasks: tasks},
	}
}

func TestWorkerStateRegistrationAndIdempotentUpdate(t *testing.T) {
	state := newWorkerState()
	task := &pb.TaskCapability{TaskName: "a", TaskVersion: "1", Invocation: "value", Codecs: []string{"msgpack"}}
	if err := state.register(registration("w", "go", 1, task)); err != nil {
		t.Fatal(err)
	}
	if err := state.updateTasks(&pb.TaskRegistration{WorkerId: "w", Generation: 1, Tasks: []*pb.TaskCapability{task}}); err != nil {
		t.Fatalf("identical update must be idempotent: %v", err)
	}
}

func TestWorkerStateRejectsConflictingGeneration(t *testing.T) {
	state := newWorkerState()
	if err := state.register(registration("w", "go", 1)); err != nil {
		t.Fatal(err)
	}
	err := state.updateTasks(&pb.TaskRegistration{WorkerId: "w", Generation: 1, Tasks: []*pb.TaskCapability{{
		TaskName: "a", TaskVersion: "1", Invocation: "value", Codecs: []string{"msgpack"},
	}}})
	if pe, ok := err.(*protocol.ProtocolError); !ok || pe.Code != protocol.TaskConflict {
		t.Fatalf("got %v, want task_conflict", err)
	}
}

func TestWorkerStateFencesPullIdentityAndGeneration(t *testing.T) {
	state := newWorkerState()
	if err := state.register(registration("w", "go", 3)); err != nil {
		t.Fatal(err)
	}
	for _, request := range []*pb.PullRequest{
		{WorkerId: "other", CapabilityGeneration: 3},
		{WorkerId: "w", CapabilityGeneration: 2},
	} {
		if err := state.validatePull(request); err == nil {
			t.Fatalf("accepted mismatched pull: %+v", request)
		}
	}
	if err := state.validatePull(&pb.PullRequest{WorkerId: "w", CapabilityGeneration: 3}); err != nil {
		t.Fatalf("matching pull rejected: %v", err)
	}
}
