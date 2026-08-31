import React from 'react';
import NexusDeskTemplate from './NexusDeskTemplate.jsx';
import './nexusDesk.css';
import { OperationalApiError, operationalApi } from '../../operationalApi';
import { getLive, reloadLive, subscribeLive } from '../liveStore';
import { buildLiveView } from '../liveView';

const DESIGN_WIDTH = 1920;

export default class NexusDesk extends React.Component {
  state = {
    now: new Date(),
    live: getLive(),
    selectedIncidentId: null,
    reason: '',
    confirmed: false,
    busy: false,
    message: null,
    packCode: '',
    windowName: '',
  };

  componentDidMount() {
    this.__prevRootFontSize = document.documentElement.style.fontSize;
    document.documentElement.style.fontSize = 'calc(100vw / ' + DESIGN_WIDTH + ' * 16)';
    this.unsub = subscribeLive(() => this.setState({ live: getLive() }));
    this.timer = setInterval(() => this.setState({ now: new Date() }), 1000);
  }

  componentWillUnmount() {
    if (this.unsub) this.unsub();
    clearInterval(this.timer);
    document.documentElement.style.fontSize = this.__prevRootFontSize || '';
  }

  view() {
    return buildLiveView(this.state.live, this.state.selectedIncidentId, this.state.now.getTime());
  }

  async decide(action) {
    const view = this.view();
    const rec = view.recommendation;
    if (!rec || this.state.busy) return;
    if (action !== 'approve' && this.state.reason.trim().length < 4) {
      this.setState({ message: 'Write a short reason. It is recorded with the decision.' });
      return;
    }
    if (action === 'approve' && !this.state.confirmed) {
      this.setState({ message: 'Confirm you have read the dissent and the stated limitations.' });
      return;
    }
    this.setState({ busy: true, message: null });
    const reasonCode = action === 'approve'
      ? 'EVIDENCE_AND_CONSTRAINTS_REVIEWED'
      : action === 'request_revision'
        ? 'REVISION_REQUESTED'
        : action === 'escalate'
          ? 'ESCALATED'
          : 'REJECTED';
    try {
      await operationalApi.decide(rec, action, reasonCode, this.state.reason.trim() || undefined);
      this.setState({ reason: '', confirmed: false });
      await reloadLive();
    } catch (reason) {
      this.setState({
        message: reason instanceof OperationalApiError ? reason.message : 'The decision could not be recorded.',
      });
    } finally {
      this.setState({ busy: false });
    }
  }

  async openWindow() {
    const view = this.view();
    const pack = view.packs.find(item => item.packCode === this.state.packCode) ?? view.packs[0];
    if (!pack) return;
    this.setState({ busy: true, message: null });
    try {
      await operationalApi.openOperatingWindow({
        packCode: pack.packCode,
        name: this.state.windowName.trim() || `${pack.name} — ${new Date().toLocaleDateString()}`,
        locationName: 'Auburn, Alabama',
      });
      await reloadLive();
    } catch (reason) {
      this.setState({
        message: reason instanceof OperationalApiError ? reason.message : 'The operating window could not be opened.',
      });
    } finally {
      this.setState({ busy: false });
    }
  }

  renderVals() {
    const pad = n => String(n).padStart(2, '0');
    const d = this.state.now;
    const view = this.view();
    const dissentName = view.desks.find(item => item.statusLabel === 'Dissent')?.name;
    return {
      clock: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
      view,
      reason: this.state.reason,
      confirmed: this.state.confirmed,
      busy: this.state.busy,
      message: this.state.message,
      packCode: this.state.packCode || view.packs[0]?.packCode || '',
      windowName: this.state.windowName,
      confirmLabel: dissentName
        ? `I have read the dissent from ${dissentName} and the stated limitations.`
        : 'I reviewed the cited sources, any feed warnings, and the stated limitations.',
      approveLabel: view.busy ? 'Recording…' : (view.canDecide ? 'Approve · record responsibility' : 'Nothing to approve'),
      onReason: ev => this.setState({ reason: ev.target.value }),
      onConfirm: () => this.setState({ confirmed: !this.state.confirmed }),
      onSelect: id => this.setState({ selectedIncidentId: id, confirmed: false, message: null }),
      onApprove: () => void this.decide('approve'),
      onSendBack: () => void this.decide('request_revision'),
      onEscalate: () => void this.decide('escalate'),
      onDecline: () => void this.decide('reject'),
      onPack: ev => this.setState({ packCode: ev.target.value }),
      onWindowName: ev => this.setState({ windowName: ev.target.value }),
      onOpenWindow: () => void this.openWindow(),
    };
  }

  render() {
    return <NexusDeskTemplate vals={this.renderVals()} />;
  }
}
