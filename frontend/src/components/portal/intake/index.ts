// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The intake form's public surface: one component, plus its props.
 *
 * A host mounts `PortalIntakeFlow` and hands it a live portal session token.
 * Everything else in this directory is the form's own business.
 */

export { PortalIntakeFlow } from "./PortalIntakeFlow"
export type { PortalIntakeFlowProps } from "./PortalIntakeFlow"
