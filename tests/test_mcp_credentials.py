"""HASE MCP architecture, Phase 3: providers.mcp.credentials table-driven
tests. Zero Home Assistant/third-party imports in the module under test -
stdlib only - so it loads as a plain standalone module, no AST-stripping
or package machinery needed.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "hase" / "providers" / "mcp" / "credentials.py"

_spec = importlib.util.spec_from_file_location("hase_mcp_credentials", MODULE_PATH)
credentials = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = credentials  # dataclasses needs the module registered before exec
_spec.loader.exec_module(credentials)

generate_token = credentials.generate_token
generate_secret = credentials.generate_secret
compute_verifier = credentials.compute_verifier
display_suffix = credentials.display_suffix
issue_credential = credentials.issue_credential
verify = credentials.verify
BridgeIdentity = credentials.BridgeIdentity
CredentialIndex = credentials.CredentialIndex


class TokenAndSecretGenerationTests(unittest.TestCase):
    def test_tokens_are_high_entropy_and_unique(self):
        tokens = {generate_token() for _ in range(200)}
        self.assertEqual(len(tokens), 200)
        self.assertTrue(all(len(t) >= 32 for t in tokens))

    def test_secrets_are_bytes_and_unique(self):
        secrets_ = {generate_secret() for _ in range(50)}
        self.assertEqual(len(secrets_), 50)
        self.assertTrue(all(isinstance(s, bytes) and len(s) >= 32 for s in secrets_))


class VerifierComputationTests(unittest.TestCase):
    def test_deterministic_for_the_same_secret_and_token(self):
        secret = generate_secret()
        token = generate_token()
        self.assertEqual(compute_verifier(secret, token), compute_verifier(secret, token))

    def test_different_tokens_yield_different_verifiers(self):
        secret = generate_secret()
        self.assertNotEqual(compute_verifier(secret, "token-a"), compute_verifier(secret, "token-b"))

    def test_different_secrets_yield_different_verifiers_for_the_same_token(self):
        token = generate_token()
        self.assertNotEqual(compute_verifier(generate_secret(), token), compute_verifier(generate_secret(), token))

    def test_verifier_never_contains_the_plaintext_token(self):
        secret = generate_secret()
        token = "sk-super-secret-value-xyz"
        verifier = compute_verifier(secret, token)
        self.assertNotIn(token, verifier)


class DisplaySuffixTests(unittest.TestCase):
    def test_suffix_is_short_and_a_true_suffix_of_the_token(self):
        token = generate_token()
        suffix = display_suffix(token)
        self.assertTrue(token.endswith(suffix))
        self.assertLess(len(suffix), len(token) - 10)

    def test_suffix_alone_cannot_reconstruct_the_token(self):
        token = generate_token()
        suffix = display_suffix(token)
        self.assertNotEqual(suffix, token)
        self.assertGreater(len(token) - len(suffix), 20)


class IssueCredentialTests(unittest.TestCase):
    def test_plaintext_token_is_returned_only_from_issue_credential_itself(self):
        secret = generate_secret()
        issued = issue_credential(secret)
        # The verifier/suffix, the only two fields a caller would persist,
        # never contain the plaintext token substring.
        self.assertNotIn(issued.token, issued.verifier)
        self.assertNotEqual(issued.suffix, issued.token)
        self.assertEqual(issued.verifier, compute_verifier(secret, issued.token))
        self.assertEqual(issued.suffix, display_suffix(issued.token))

    def test_repr_of_an_issued_credential_does_not_hide_the_token(self):
        # Documents the actual contract: IssuedCredential.token IS the
        # full secret, by design, for its one intended caller (the config
        # flow step that shows it to the user once) - the "never log it"
        # obligation belongs to callers (see CredentialIndexTests below
        # for the piece that must never even see it), not to this
        # dataclass pretending to redact itself.
        issued = issue_credential(generate_secret())
        self.assertIn(issued.token, repr(issued))


class VerifyTests(unittest.TestCase):
    def test_correct_token_verifies(self):
        secret = generate_secret()
        issued = issue_credential(secret)
        self.assertTrue(verify(secret, issued.token, expected_verifier=issued.verifier))

    def test_wrong_token_is_rejected(self):
        secret = generate_secret()
        issued = issue_credential(secret)
        self.assertFalse(verify(secret, "not-the-right-token", expected_verifier=issued.verifier))

    def test_correct_token_with_wrong_secret_is_rejected(self):
        secret = generate_secret()
        issued = issue_credential(secret)
        self.assertFalse(verify(generate_secret(), issued.token, expected_verifier=issued.verifier))


class CredentialIndexTests(unittest.TestCase):
    def test_deterministic_verifier_lookup_resolves_the_correct_bridge(self):
        secret = generate_secret()
        index = CredentialIndex()
        jan = issue_credential(secret)
        nils = issue_credential(secret)
        index.put(jan.verifier, BridgeIdentity(entry_id="server-1", subentry_id="jan"))
        index.put(nils.verifier, BridgeIdentity(entry_id="server-1", subentry_id="nils"))

        self.assertEqual(index.resolve(secret, jan.token), BridgeIdentity("server-1", "jan"))
        self.assertEqual(index.resolve(secret, nils.token), BridgeIdentity("server-1", "nils"))

    def test_credential_lookup_never_takes_or_needs_a_client_supplied_bridge_id(self):
        # CredentialIndex.resolve's signature itself is the proof: it
        # takes only (secret, token) - there is no bridge_id parameter for
        # a client to supply, honestly or dishonestly.
        import inspect
        params = list(inspect.signature(CredentialIndex.resolve).parameters)
        self.assertNotIn("bridge_id", params)
        self.assertEqual(params, ["self", "secret", "token"])

    def test_unknown_token_resolves_to_none(self):
        secret = generate_secret()
        index = CredentialIndex()
        index.put(issue_credential(secret).verifier, BridgeIdentity("server-1", "jan"))
        self.assertIsNone(index.resolve(secret, "totally-unknown-token"))

    def test_bridge_a_credential_never_resolves_bridge_b(self):
        secret = generate_secret()
        index = CredentialIndex()
        jan = issue_credential(secret)
        master = issue_credential(secret)
        index.put(jan.verifier, BridgeIdentity("server-1", "jan"))
        index.put(master.verifier, BridgeIdentity("server-1", "master"))

        resolved = index.resolve(secret, jan.token)
        self.assertEqual(resolved.subentry_id, "jan")
        self.assertNotEqual(resolved.subentry_id, "master")

    def test_regeneration_removes_old_verifier_synchronously(self):
        secret = generate_secret()
        index = CredentialIndex()
        old = issue_credential(secret)
        identity = BridgeIdentity("server-1", "jan")
        index.put(old.verifier, identity)
        self.assertEqual(index.resolve(secret, old.token), identity)

        # Regeneration, step by step, exactly as the binding design
        # requires: remove old, add new - both synchronous, no in-between
        # state where the old token still resolves.
        new = issue_credential(secret)
        index.remove(old.verifier)
        index.put(new.verifier, identity)

        self.assertIsNone(index.resolve(secret, old.token))  # old token fails immediately
        self.assertEqual(index.resolve(secret, new.token), identity)  # new token works immediately

    def test_bridge_deletion_synchronously_removes_its_verifier(self):
        secret = generate_secret()
        index = CredentialIndex()
        issued = issue_credential(secret)
        index.put(issued.verifier, BridgeIdentity("server-1", "jan"))
        self.assertIsNotNone(index.resolve(secret, issued.token))

        index.remove(issued.verifier)
        self.assertIsNone(index.resolve(secret, issued.token))

    def test_deleting_one_bridge_never_affects_another(self):
        secret = generate_secret()
        index = CredentialIndex()
        jan = issue_credential(secret)
        master = issue_credential(secret)
        index.put(jan.verifier, BridgeIdentity("server-1", "jan"))
        index.put(master.verifier, BridgeIdentity("server-1", "master"))

        index.remove(jan.verifier)
        self.assertIsNone(index.resolve(secret, jan.token))
        self.assertIsNotNone(index.resolve(secret, master.token))

    def test_index_holds_no_session_state_resolution_is_purely_per_call(self):
        # No connect()/session object anywhere in this API - every
        # resolve() call is independent and stateless from the index's own
        # point of view, which is exactly what "must support per-request
        # credential resolution, not session-lifetime auth" requires.
        secret = generate_secret()
        index = CredentialIndex()
        issued = issue_credential(secret)
        identity = BridgeIdentity("server-1", "jan")
        index.put(issued.verifier, identity)

        for _ in range(5):
            self.assertEqual(index.resolve(secret, issued.token), identity)
        index.remove(issued.verifier)
        for _ in range(5):
            self.assertIsNone(index.resolve(secret, issued.token))


if __name__ == "__main__":
    unittest.main()
