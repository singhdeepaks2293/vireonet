import { useQuery } from "@tanstack/react-query";
import { request } from "../lib/api";

export interface ControllerInfo { name: string; vendor: string; platforms: string[]; native_upgrade: boolean; fake: boolean; status: string; detail: string }
export interface ControllerDeviceRow { id: string; name: string; platform: string; version: string; serial: string; model: string; reachable: boolean }
export interface ControllerImageRow { id: string; name: string; version: string; platform: string }

export const controllersApi = {
  list: () => request<{ items: ControllerInfo[] }>("/controllers"),
  devices: (name: string) => request<{ items: ControllerDeviceRow[] }>(`/controllers/${encodeURIComponent(name)}/devices`),
  images: (name: string) => request<{ items: ControllerImageRow[] }>(`/controllers/${encodeURIComponent(name)}/images`),
};

export function useControllers() {
  return useQuery({ queryKey: ["controllers"], queryFn: () => controllersApi.list(), staleTime: 60_000 });
}
