package controlserver

import (
	"context"
	"net"
	"path/filepath"
	"testing"
	"time"

	"github.com/sanketn26/taskwire/agent/pkg/protocol"
	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

const maxMessageBytes = 16 * 1024 * 1024

// startServer brings up a control server on a temp-dir socket and returns a
// connected client. Everything is torn down when the test ends.
func startServer(t *testing.T) pb.TaskwireControlClient {
	t.Helper()

	socket := filepath.Join(t.TempDir(), "agent.sock")
	server, err := New(socket, "test-version", maxMessageBytes)
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	served := make(chan error, 1)
	go func() { served <- server.Serve() }()

	conn, err := grpc.NewClient(
		"unix:"+socket,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}

	t.Cleanup(func() {
		_ = conn.Close()
		_ = server.Close()
		select {
		case err := <-served:
			if err != nil {
				t.Errorf("Serve returned %v", err)
			}
		case <-time.After(5 * time.Second):
			t.Error("Serve did not return after Close")
		}
	})

	return pb.NewTaskwireControlClient(conn)
}

func callerContext(t *testing.T, pairs ...string) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)
	return metadata.AppendToOutgoingContext(ctx, pairs...)
}

func TestStatusServesSnapshot(t *testing.T) {
	client := startServer(t)
	ctx := callerContext(t, protocol.MetadataRole, protocol.RoleAdmin)

	snapshot, err := client.Status(ctx, &pb.StatusRequest{})
	if err != nil {
		t.Fatalf("Status: %v", err)
	}
	if snapshot.Version != "test-version" {
		t.Fatalf("got version %q, want %q", snapshot.Version, "test-version")
	}
	if !snapshot.Ready {
		t.Fatal("agent reported not ready")
	}
	if snapshot.Pid == 0 {
		t.Fatal("snapshot carries no pid")
	}
}

func TestStatusRequiresRoleMetadata(t *testing.T) {
	client := startServer(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := client.Status(ctx, &pb.StatusRequest{})
	if status.Code(err) != codes.FailedPrecondition {
		t.Fatalf("got %v, want FailedPrecondition", err)
	}
	if detail := protocol.ErrorFromStatus(err); detail == nil || detail.Code != protocol.NotRegistered {
		t.Fatalf("missing not_registered detail: %v", err)
	}
}

func TestRoleForbiddenCarriesTaskwireDetail(t *testing.T) {
	client := startServer(t)
	ctx := callerContext(t, protocol.MetadataRole, protocol.RoleAdmin)

	_, err := client.Submit(ctx, &pb.TaskEnvelope{})
	if status.Code(err) != codes.PermissionDenied {
		t.Fatalf("got %v, want PermissionDenied", err)
	}
	detail := protocol.ErrorFromStatus(err)
	if detail == nil || detail.Code != protocol.RoleForbidden {
		t.Fatalf("missing role_forbidden detail: %v", err)
	}
	if detail.Retryable {
		t.Fatal("role_forbidden must not be retryable")
	}
}

func TestWorkStreamRegistersWorker(t *testing.T) {
	client := startServer(t)
	ctx := callerContext(t, protocol.MetadataRole, protocol.RoleWorker)

	stream, err := client.Work(ctx)
	if err != nil {
		t.Fatalf("Work: %v", err)
	}

	registration := &pb.WorkerRegistration{
		WorkerId: "w-1", Runtime: "python", RuntimeVersion: "3.12", SdkVersion: "0.1.0",
		Codecs: []string{"msgpack"},
		Tasks: &pb.TaskRegistration{
			WorkerId: "w-1", Generation: 1,
			Tasks: []*pb.TaskCapability{{
				TaskName: "a", TaskVersion: "1", Invocation: "value", Codecs: []string{"msgpack"},
			}},
		},
	}
	if err := stream.Send(&pb.WorkerMessage{
		Body: &pb.WorkerMessage_Register{Register: registration},
	}); err != nil {
		t.Fatalf("send registration: %v", err)
	}

	message, err := stream.Recv()
	if err != nil {
		t.Fatalf("recv: %v", err)
	}
	registered := message.GetRegistered()
	if registered == nil {
		t.Fatalf("expected a registration response, got %v", message)
	}
	if registered.WorkerId != "w-1" || registered.Generation != 1 || registered.Accepted != 1 {
		t.Fatalf("unexpected registration response: %+v", registered)
	}
}

func TestWorkStreamRejectsUnregisteredFirstMessage(t *testing.T) {
	client := startServer(t)
	ctx := callerContext(t, protocol.MetadataRole, protocol.RoleWorker)

	stream, err := client.Work(ctx)
	if err != nil {
		t.Fatalf("Work: %v", err)
	}
	if err := stream.Send(&pb.WorkerMessage{
		Body: &pb.WorkerMessage_Heartbeat{Heartbeat: &pb.HeartbeatRequest{LeaseId: make([]byte, 16)}},
	}); err != nil {
		t.Fatalf("send heartbeat: %v", err)
	}

	_, err = stream.Recv()
	if status.Code(err) != codes.FailedPrecondition {
		t.Fatalf("got %v, want FailedPrecondition", err)
	}
	if detail := protocol.ErrorFromStatus(err); detail == nil || detail.Code != protocol.NotRegistered {
		t.Fatalf("missing not_registered detail: %v", err)
	}
}

func TestWorkStreamHeartbeatAcknowledged(t *testing.T) {
	client := startServer(t)
	ctx := callerContext(t, protocol.MetadataRole, protocol.RoleWorker)

	stream, err := client.Work(ctx)
	if err != nil {
		t.Fatalf("Work: %v", err)
	}
	if err := stream.Send(&pb.WorkerMessage{
		Body: &pb.WorkerMessage_Register{Register: &pb.WorkerRegistration{
			WorkerId: "w-1", Runtime: "go", RuntimeVersion: "1.26", SdkVersion: "0.1.0",
			Codecs: []string{"msgpack"},
			Tasks:  &pb.TaskRegistration{WorkerId: "w-1", Generation: 1},
		}},
	}); err != nil {
		t.Fatalf("send registration: %v", err)
	}
	if _, err := stream.Recv(); err != nil {
		t.Fatalf("recv registration response: %v", err)
	}

	leaseID := make([]byte, 16)
	leaseID[0] = 9
	if err := stream.Send(&pb.WorkerMessage{
		Body: &pb.WorkerMessage_Heartbeat{Heartbeat: &pb.HeartbeatRequest{LeaseId: leaseID}},
	}); err != nil {
		t.Fatalf("send heartbeat: %v", err)
	}

	message, err := stream.Recv()
	if err != nil {
		t.Fatalf("recv heartbeat ack: %v", err)
	}
	ack := message.GetHeartbeatAck()
	if ack == nil || string(ack.LeaseId) != string(leaseID) {
		t.Fatalf("unexpected heartbeat response: %v", message)
	}
}

func TestCloseRemovesSocketFile(t *testing.T) {
	socket := filepath.Join(t.TempDir(), "agent.sock")
	server, err := New(socket, "v", maxMessageBytes)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	go func() { _ = server.Serve() }()

	if _, err := net.Dial("unix", socket); err != nil {
		t.Fatalf("socket not accepting connections: %v", err)
	}
	if err := server.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if _, err := net.Dial("unix", socket); err == nil {
		t.Fatal("socket still accepts connections after Close")
	}
}
