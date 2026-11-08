'use client';

import React, { useEffect, useState } from 'react';
import { SpinnerGap } from '@phosphor-icons/react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';
import { getVault, updateVault } from '@/lib/api/applier';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type Nested = Record<string, unknown>;

function asRecord(value: unknown): Nested {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Nested) : {};
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export function ApplyProfileDialog({ open, onOpenChange }: Props) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [answersCount, setAnswersCount] = useState(0);
  const [gmailConfigured, setGmailConfigured] = useState(false);
  const [jobrightConfigured, setJobrightConfigured] = useState(false);

  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [linkedin, setLinkedin] = useState('');
  const [github, setGithub] = useState('');
  const [sponsorship, setSponsorship] = useState(true);
  const [visa, setVisa] = useState('STEM OPT');
  const [howHeard, setHowHeard] = useState('Online job board');
  const [gmailAppPassword, setGmailAppPassword] = useState('');
  const [workdayPassword, setWorkdayPassword] = useState('');
  const [jobrightEmail, setJobrightEmail] = useState('');
  const [jobrightPassword, setJobrightPassword] = useState('');

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSaved(false);
    setLoading(true);
    void (async () => {
      try {
        const vault = await getVault();
        const personal = asRecord(vault.personal);
        const auth = asRecord(vault.work_authorization || vault.work_auth);
        const creds = asRecord(vault.credentials);
        const jobright = asRecord(vault.jobright);
        const std = asRecord(vault.standard_answers);
        setEmail(str(personal.email));
        setPhone(str(personal.phone));
        setLinkedin(str(personal.linkedin || personal.linkedin_url));
        setGithub(str(personal.github));
        setSponsorship(Boolean(auth.needs_sponsorship ?? auth.requires_sponsorship ?? true));
        setVisa(str(auth.visa_status) || 'STEM OPT');
        setHowHeard(str(std.how_did_you_hear) || 'Online job board');
        setGmailAppPassword(str(creds.gmail_app_password));
        setWorkdayPassword(str(creds.password));
        setJobrightEmail(str(jobright.email));
        setJobrightPassword(str(jobright.password));
        setAnswersCount(Number(vault.answers_count || 0));
        setGmailConfigured(Boolean(vault.gmail_configured));
        setJobrightConfigured(Boolean(vault.jobright_configured));
      } catch {
        setError('Could not load Apply Profile');
      } finally {
        setLoading(false);
      }
    })();
  }, [open]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const savedVault = await updateVault({
        personal: { email, phone, linkedin, github },
        work_authorization: {
          needs_sponsorship: sponsorship,
          authorized_to_work: true,
          visa_status: visa,
        },
        standard_answers: { how_did_you_hear: howHeard },
        credentials: {
          gmail_app_password: gmailAppPassword,
          password: workdayPassword,
        },
        jobright: {
          email: jobrightEmail,
          password: jobrightPassword,
        },
      });
      setAnswersCount(Number(savedVault.answers_count || answersCount));
      setGmailConfigured(Boolean(savedVault.gmail_configured));
      setJobrightConfigured(Boolean(savedVault.jobright_configured));
      setSaved(true);
    } catch {
      setError('Could not save Apply Profile');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Apply Profile</DialogTitle>
          <DialogDescription>
            Saved once, reused on every form. Resume contact info and project bullets fill
            the rest automatically. Never paste passwords in chat — only here.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          {loading ? (
            <p className="font-mono text-xs text-steel">Loading…</p>
          ) : (
            <>
              <p className="font-mono text-[10px] text-steel">
                Learned answers: {answersCount}
                {gmailConfigured ? ' · Gmail ready' : ''}
                {jobrightConfigured ? ' · Jobright ready' : ''}
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="apply-email">Email</Label>
                  <Input
                    id="apply-email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    autoComplete="email"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="apply-phone">Phone</Label>
                  <Input
                    id="apply-phone"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    autoComplete="tel"
                  />
                </div>
                <div className="space-y-1 col-span-2">
                  <Label htmlFor="apply-linkedin">LinkedIn</Label>
                  <Input
                    id="apply-linkedin"
                    value={linkedin}
                    onChange={(e) => setLinkedin(e.target.value)}
                    placeholder="https://linkedin.com/in/…"
                  />
                </div>
                <div className="space-y-1 col-span-2">
                  <Label htmlFor="apply-github">GitHub</Label>
                  <Input
                    id="apply-github"
                    value={github}
                    onChange={(e) => setGithub(e.target.value)}
                    placeholder="https://github.com/…"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="apply-visa">Visa / work auth</Label>
                  <Input id="apply-visa" value={visa} onChange={(e) => setVisa(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="apply-heard">How did you hear</Label>
                  <Input
                    id="apply-heard"
                    value={howHeard}
                    onChange={(e) => setHowHeard(e.target.value)}
                  />
                </div>
              </div>
              <label className="flex items-center gap-2 font-mono text-xs">
                <input
                  type="checkbox"
                  checked={sponsorship}
                  onChange={(e) => setSponsorship(e.target.checked)}
                />
                Needs visa sponsorship
              </label>
              <div className="space-y-1">
                <Label htmlFor="apply-gmail-pw">Gmail App Password</Label>
                <Input
                  id="apply-gmail-pw"
                  type="password"
                  value={gmailAppPassword}
                  onChange={(e) => setGmailAppPassword(e.target.value)}
                  placeholder="For email-apply jobs and Workday verification codes"
                  autoComplete="new-password"
                />
                <p className="font-mono text-[10px] text-steel">
                  Google Account → Security → App passwords. Not your normal Gmail password.
                </p>
              </div>
              <div className="space-y-1">
                <Label htmlFor="apply-wd-pw">Reusable Workday password</Label>
                <Input
                  id="apply-wd-pw"
                  type="password"
                  value={workdayPassword}
                  onChange={(e) => setWorkdayPassword(e.target.value)}
                  placeholder="Same password, new account on each company site"
                  autoComplete="new-password"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="apply-jr-email">Jobright email</Label>
                  <Input
                    id="apply-jr-email"
                    value={jobrightEmail}
                    onChange={(e) => setJobrightEmail(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="apply-jr-pw">Jobright password</Label>
                  <Input
                    id="apply-jr-pw"
                    type="password"
                    value={jobrightPassword}
                    onChange={(e) => setJobrightPassword(e.target.value)}
                    autoComplete="new-password"
                  />
                </div>
              </div>
              {error && (
                <Alert tone="error">{error}</Alert>
              )}
              {saved && (
                <Alert tone="success">Saved. The next Auto-Apply run will use these answers.</Alert>
              )}
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button type="button" onClick={() => void handleSave()} disabled={saving || loading}>
            {saving ? (
              <>
                <SpinnerGap aria-hidden="true" className="animate-spin" />
                Saving
              </>
            ) : (
              'Save profile'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
