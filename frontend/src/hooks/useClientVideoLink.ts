// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  type ClientVideoLinkSetting,
  fetchClientVideoLinkSetting,
  updateClientVideoLinkSetting,
} from "@/lib/api/clientVideoLink"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** Whether the practice sends clients their video link. */
export function useClientVideoLinkSetting(token?: string) {
  return useAuthQuery<ClientVideoLinkSetting>({
    queryKey: queryKeys.clientVideoLink.all,
    queryFn: () => fetchClientVideoLinkSetting(token),
    staleTime: 5 * 60 * 1000,
  })
}

export function useUpdateClientVideoLinkSetting(token?: string) {
  return useAuthMutation<ClientVideoLinkSetting, boolean>({
    mutationFn: (send) => updateClientVideoLinkSetting(send, token),
    onSuccess: (data, _send, queryClient) => {
      queryClient.setQueryData(queryKeys.clientVideoLink.all, data)
    },
  })
}
