package service

import (
	"context"
	"log"
	"runtime"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/cryptoqueue"
)

type HeartbeatPoster interface {
	PostHeartbeat(context.Context, any) error
	RefreshConfiguration(context.Context) error
	ConfigVersion() int
}

type Heartbeater struct {
	Store           *cryptoqueue.Store
	Client          HeartbeatPoster
	RecorderVersion string
	PlatformVersion string
	Interval        time.Duration
	Logger          *log.Logger
	Now             func() time.Time
}

func (h *Heartbeater) Run(ctx context.Context) error {
	if h.Interval <= 0 {
		h.Interval = 5 * time.Minute
	}
	if h.Now == nil {
		h.Now = time.Now
	}
	ticker := time.NewTicker(h.Interval)
	defer ticker.Stop()
	for {
		h.send(ctx)
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}

func (h *Heartbeater) send(ctx context.Context) {
	configCtx, configCancel := context.WithTimeout(ctx, 8*time.Second)
	if err := h.Client.RefreshConfiguration(configCtx); err != nil {
		h.diagnostic("configuration_refresh_failed")
	}
	configCancel()
	summary, err := h.Store.Summary(ctx)
	if err != nil {
		h.diagnostic("summary_failed")
		return
	}
	delivery := "healthy"
	if summary.BlockedBatches > 0 {
		delivery = "blocked"
	} else if summary.QueuedBatches > 0 {
		delivery = "delayed"
	}
	oldest := int64(0)
	if summary.OldestCreatedAtMS != nil {
		oldest = h.Now().Unix() - (*summary.OldestCreatedAtMS / 1000)
		if oldest < 0 {
			oldest = 0
		}
	}
	payload := map[string]any{
		"recorder_version":          h.RecorderVersion,
		"platform":                  runtime.GOOS,
		"platform_version":          h.PlatformVersion,
		"architecture":              runtime.GOARCH,
		"config_version":            h.Client.ConfigVersion(),
		"adapter_state":             "ready",
		"queue_batches":             summary.QueuedBatches,
		"queue_bytes":               summary.QueuedBytes,
		"oldest_queued_age_seconds": oldest,
		"delivery_state":            delivery,
	}
	requestCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := h.Client.PostHeartbeat(requestCtx, payload); err != nil {
		h.diagnostic("heartbeat_failed")
	}
}

func (h *Heartbeater) diagnostic(kind string) {
	if h.Logger != nil {
		h.Logger.Printf("recorder control diagnostic=%s", kind)
	}
}
